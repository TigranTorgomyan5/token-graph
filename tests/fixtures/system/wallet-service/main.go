package main

import (
	"github.com/gin-gonic/gin"
	"github.com/nats-io/nats.go"
	"github.com/segmentio/kafka-go"
)

func main() {
	nc, _ := nats.Connect(nats.DefaultURL)
	nc.Subscribe("wallet.*.debit", func(m *nats.Msg) {})
	w := &kafka.Writer{Addr: kafka.TCP("kafka:9092"), Topic: "wallet-events"}
	_ = w
	r := gin.Default()
	r.GET("/balance/:user", func(c *gin.Context) {})
}
