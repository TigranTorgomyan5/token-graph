"""Adapter helpers behind the deeper call-flow: middleware mounted for every route, interface-typed fields."""
from pathlib import Path
import importlib.util
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "token-graph" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("agent_frameworks", SCRIPTS / "agent_frameworks.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)


class FlowDepthTests(unittest.TestCase):
    def test_go_middleware_named_and_inline(self):
        lines = ["api.Use(internalauth.Gate(cfg.Secret, log))", "root.Use(httpx.TraceIDMiddleware)",
                 "root.Use(func(next http.Handler) http.Handler {", "// api.Use(old.Gone)"]
        self.assertEqual(fw.middleware_uses("app.go", lines), [(1, "internalauth", "Gate"), (2, "httpx", "TraceIDMiddleware")])

    def test_js_csharp_python_middleware(self):
        self.assertEqual(fw.middleware_uses("app.js", ["app.use(requireAuth)"]), [(1, None, "requireAuth")])
        self.assertEqual(fw.middleware_uses("Program.cs", ["app.UseMiddleware<SecretGate>();"]), [(1, None, "SecretGate")])
        self.assertEqual(fw.middleware_uses("main.py", ["app.add_middleware(AuthMiddleware)"]), [(1, None, "AuthMiddleware")])

    def test_interface_and_receiver(self):
        self.assertTrue(fw.is_interface_type("x.go", "type Limiter interface {", "Limiter"))
        self.assertFalse(fw.is_interface_type("x.go", "type Limiter struct {", "Limiter"))
        self.assertEqual(fw.method_receiver("x.go", "func (l *redisLimiter) Check(ctx context.Context) error {", "Check"), "redisLimiter")
        self.assertIsNone(fw.method_receiver("x.go", "func (l *redisLimiter) Other() {", "Check"))


if __name__ == "__main__":
    unittest.main()
