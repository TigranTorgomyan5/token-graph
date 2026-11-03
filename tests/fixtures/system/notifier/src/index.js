const { Kafka } = require('kafkajs');
const kafka = new Kafka({ brokers: ['kafka:9092'] });
const consumer = kafka.consumer({ groupId: 'notifier' });
async function run() {
  await consumer.subscribe({ topic: 'wallet-events', fromBeginning: true });
  const res = await fetch('http://wallet-service:8080/balance/42');
}
