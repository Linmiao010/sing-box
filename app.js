'use strict';

const http = require('node:http');

function handleHealth(req, res) {
    const ok = req.url === '/health' && (req.method === 'GET' || req.method === 'HEAD');
    res.writeHead(ok ? 200 : 404, {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'no-store'
    });
    res.end(ok ? 'ok\n' : 'not found\n');
}

// Passenger loads app.js with require() and hooks the first listen() call.
http.createServer(handleHealth).listen(Number(process.env.PORT || 3000), '127.0.0.1');
