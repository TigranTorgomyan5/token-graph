fun main() {
    embeddedServer(Netty, port = 8080) {
        routing {
            get("/health") { call.respondText("ok") }
            route("/api") {
                post("/login") { call.respond(login()) }
            }
        }
    }.start(wait = true)
}
