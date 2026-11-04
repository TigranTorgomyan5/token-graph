// Cart service: HTTP API over the cart store.
const express = require('express');
const axios = require('axios');
const cart = require('./routes/cart');

const app = express();
app.use('/api/cart', cart);
app.get('/health', (req, res) => res.json({ ok: true }));

async function main() {
  await axios.get('/upstream/config');
  const server = app.listen(3000);
  process.on('SIGTERM', () => server.close());
}

main();
