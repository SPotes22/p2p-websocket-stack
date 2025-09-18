export function serializeData(data) {
    return JSON.stringify(data);
}

export function deserializeData(data) {
    return JSON.parse(data);
}

export function createMessage(type, payload) {
    return {
        type: type,
        payload: payload,
        timestamp: new Date().toISOString()
    };
}

export function parseMessage(message) {
    const { type, payload, timestamp } = message;
    return { type, payload, timestamp: new Date(timestamp) };
}