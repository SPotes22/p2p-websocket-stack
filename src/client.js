document.addEventListener("DOMContentLoaded", () => {
    const status = document.getElementById("status");
    const messages = document.getElementById("messages");
    const sendBtn = document.getElementById("sendBtn");
    const input = document.getElementById("input");

    let socket;

    function connect() {
        socket = new WebSocket("ws://192.168.60.104:12345");

        socket.onopen = () => {
            status.innerText = "Conectado";
            socket.send("Hola desde el navegador");
        };

        socket.onmessage = (event) => {
            const msg = document.createElement("div");
            msg.innerText = "Servidor: " + event.data;
            messages.appendChild(msg);
        };

        socket.onclose = () => {
            status.innerText = "Desconectado";
        };

        socket.onerror = (error) => {
            status.innerText = "Error de conexión";
            console.error("WebSocket error:", error);
        };
    }

    sendBtn.onclick = () => {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(input.value);
            const msg = document.createElement("div");
            msg.innerText = "Tú: " + input.value;
            messages.appendChild(msg);
            input.value = "";
        }
    };

    connect();
});