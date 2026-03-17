import os
import hashlib
import time
import uuid
from argon2 import PasswordHasher
from flask import Flask, request, redirect, url_for, render_template
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from flask_argon2 import Argon2

# --- CONFIGURACIÓN INICIAL ---
app = Flask(__name__)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    async_mode='threading'   # evita bloqueos con emit entre hilos
)
app.secret_key = os.environ.get("SECRET_KEY", "a-very-secret-key-for-dev")
argon2 = Argon2(app)
ph = PasswordHasher()
login_manager = LoginManager(app)
login_manager.login_view = 'login'

print("✅ Configuración inicial completada...")

# --- CARPETA DE CUARENTENA (uploads) ---
UPLOAD_FOLDER = './cuarentena'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- USUARIOS BASE (cargados desde env) ---
users = {
    os.getenv("ADMIN_USER",  "admin"):   {"password": ph.hash(os.getenv("ADMIN_PASS",  "admin123")),   "role": "administrator"},
    os.getenv("CLIENT_USER", "cliente"): {"password": ph.hash(os.getenv("CLIENT_PASS", "cliente123")), "role": "cliente"},
    os.getenv("USR_USER",    "usuario"): {"password": ph.hash(os.getenv("USR_PASS",    "usuario123")), "role": "usuario"},
}
print(f"✅ Usuarios cargados: {', '.join(users.keys())}")

# ============= ESTADO GLOBAL P2P =============
# {peer_hash: {nombre, ip, session_id, ultimo_seen}}
peers_conectados: dict = {}

# Historial de mensajes por "conversación" (clave: frozenset de dos hashes)
# Estructura: {conv_key: [mensaje, ...]}
mensajes_historial: dict = {}

PEER_TIMEOUT = 300   # segundos sin ping → peer caído
MAX_HISTORIAL = 100  # mensajes por conversación


# ============= HELPERS =============

def generar_peer_hash(username: str) -> str:
    """Hash determinista: mismo usuario → mismo hash siempre."""
    data = f"{username}{app.secret_key}".encode()
    return hashlib.sha256(data).hexdigest()[:16]

def conv_key(hash_a: str, hash_b: str) -> str:
    """Clave de conversación independiente del orden."""
    return ":".join(sorted([hash_a, hash_b]))

def limpiar_peers_caidos():
    ahora = time.time()
    caidos = [h for h, d in list(peers_conectados.items())
              if ahora - d["ultimo_seen"] > PEER_TIMEOUT]
    for h in caidos:
        nombre = peers_conectados[h]["nombre"]
        del peers_conectados[h]
        print(f"🗑  Peer caído eliminado: {nombre} ({h})")
        socketio.emit('peer_desconectado', {'peer_hash': h})
    return len(caidos)

def peers_para(mi_hash: str) -> list:
    """Lista de peers visibles para un usuario (todos menos él mismo)."""
    return [
        {'hash': h, 'nombre': d['nombre']}
        for h, d in peers_conectados.items()
        if h != mi_hash
    ]


# ============= FLASK-LOGIN =============

class Usuario(UserMixin):
    def __init__(self, username, role):
        self.id        = username
        self.rol       = role
        self.peer_hash = generar_peer_hash(username)

@login_manager.user_loader
def load_user(user_id):
    if user_id in users:
        return Usuario(user_id, users[user_id]['role'])
    return None

print("✅ Login manager listo")


# ============= RUTAS HTTP =============

@app.route('/')
def home():
    logout_user()          # siempre empieza en login limpio
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('inicio'))
    if request.method == 'POST':
        user = request.form.get('usuario', '').strip()
        pwd  = request.form.get('clave',   '').strip()
        if user in users:
            try:
                ph.verify(users[user]['password'], pwd)
                login_user(Usuario(user, users[user]['role']))
                return redirect(url_for('inicio'))
            except Exception:
                pass
        return render_template("login.html", error="Credenciales inválidas.")
    return render_template("login.html")

@app.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/inicio')
@login_required
def inicio():
    limpiar_peers_caidos()
    return render_template('inicio.html',
                           current_user=current_user,
                           peers=peers_conectados,
                           peer_count=len(peers_conectados))

@app.route('/chat')
@login_required
def chat():
    return render_template('chat.html',
                           current_user=current_user,
                           peer_hash=current_user.peer_hash)


# ============= SOCKET.IO — CONEXIÓN =============

@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False   # rechazar conexión no autenticada

    mi_hash = generar_peer_hash(current_user.id)

    # Registrar / actualizar peer
    peers_conectados[mi_hash] = {
        "nombre":      current_user.id,
        "ip":          request.remote_addr,
        "session_id":  request.sid,
        "ultimo_seen": time.time(),
    }
    print(f"🔗 Conectado: {current_user.id} ({mi_hash}) sid={request.sid}")

    # Enviar a TODOS la lista actualizada (incluido el recién conectado)
    socketio.emit('peer_list_actualizada', {
        'peers': peers_para(mi_hash),
        'yo':    mi_hash,
    }, room=request.sid)                        # al que se conectó: su lista

    emit('peer_list_actualizada', {             # a los demás: lista sin él
        'peers': peers_para(mi_hash),
    }, broadcast=True, include_self=False)


@socketio.on('disconnect')
def handle_disconnect():
    for mi_hash, data in list(peers_conectados.items()):
        if data['session_id'] == request.sid:
            nombre = data['nombre']
            del peers_conectados[mi_hash]
            print(f"⚠️  Desconectado: {nombre} ({mi_hash})")
            emit('peer_desconectado', {'peer_hash': mi_hash},
                 broadcast=True, include_self=False)
            break


# ============= SOCKET.IO — OPERACIONES =============

@socketio.on('ping')
def handle_ping():
    if current_user.is_authenticated:
        mi_hash = generar_peer_hash(current_user.id)
        if mi_hash in peers_conectados:
            peers_conectados[mi_hash]['ultimo_seen'] = time.time()
        emit('pong')

@socketio.on('solicitar_peers')
def handle_solicitar_peers():
    if not current_user.is_authenticated:
        return
    limpiar_peers_caidos()
    mi_hash = generar_peer_hash(current_user.id)
    emit('lista_peers', {'peers': peers_para(mi_hash)})


@socketio.on('mensaje_privado')
def handle_mensaje_privado(data):
    """
    Flujo:
      A emite 'mensaje_privado' → servidor lo guarda y
      lo reenvía a B ('mensaje_recibido') y confirma a A ('mensaje_enviado')
      con el objeto completo para que ambos actualicen su UI en tiempo real.
    """
    if not current_user.is_authenticated:
        return

    emisor_hash       = generar_peer_hash(current_user.id)
    destinatario_hash = data.get('destinatario_hash', '')
    texto             = data.get('texto', '').strip()
    respondiendo_a    = data.get('respondiendo_a')

    if not texto:
        return

    if destinatario_hash not in peers_conectados:
        emit('error', {'msg': '❌ Destinatario no disponible'})
        return

    destinatario_sid = peers_conectados[destinatario_hash].get('session_id')
    if not destinatario_sid:
        emit('error', {'msg': '❌ Destinatario desconectado'})
        return

    # Construir mensaje
    now       = time.time()
    message_id = str(uuid.uuid4())
    mensaje = {
        'message_id':      message_id,
        'emisor_hash':     emisor_hash,
        'emisor_nombre':   current_user.id,
        'destinatario_hash': destinatario_hash,
        'texto':           texto,
        'timestamp':       now,
        'tiempo':          time.strftime('%H:%M:%S', time.localtime(now)),
        'respondiendo_a':  respondiendo_a,
    }

    # Guardar en historial compartido de la conversación
    clave = conv_key(emisor_hash, destinatario_hash)
    if clave not in mensajes_historial:
        mensajes_historial[clave] = []
    mensajes_historial[clave].append(mensaje)
    if len(mensajes_historial[clave]) > MAX_HISTORIAL:
        mensajes_historial[clave].pop(0)

    # Entregar al destinatario
    try:
        emit('mensaje_recibido', mensaje, room=destinatario_sid)
        print(f"📨 {current_user.id} → {peers_conectados[destinatario_hash]['nombre']}: {texto[:40]}")
    except Exception as e:
        print(f"❌ Error enviando a destinatario: {e}")
        emit('error', {'msg': 'Error al enviar mensaje'})
        return

    # Confirmar al emisor con el objeto completo (para que lo muestre en su UI)
    emit('mensaje_enviado', mensaje)


@socketio.on('obtener_historial')
def handle_obtener_historial(data):
    if not current_user.is_authenticated:
        return
    mi_hash   = generar_peer_hash(current_user.id)
    peer_hash = data.get('peer_hash', '')
    clave     = conv_key(mi_hash, peer_hash)
    historial = mensajes_historial.get(clave, [])
    emit('historial_mensajes', {'mensajes': historial})


# ============= INICIO =============
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Servidor en puerto {port} — usuarios: {', '.join(users.keys())}")
    socketio.run(app, host='0.0.0.0', port=port, debug=True)

application = app
