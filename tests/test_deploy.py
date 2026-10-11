import importlib.util
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

spec = importlib.util.spec_from_file_location("deploy_script", Path(__file__).resolve().parents[1] / "scripts" / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeployTests(unittest.TestCase):
    def run_script(self, body=b'{"message":"Application deployed successfully"}', content_type="application/json", latest="abc", error=None):
        response = Mock()
        response.status = 200
        response.headers = {"Content-Type": content_type}
        response.read.return_value = body
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"DOKPLOY_DEPLOY_URL": "https://example.com/api/deploy/test-secret", "GITHUB_EVENT_PATH": "event.json", "GITHUB_SHA": "abc"}),
            patch.object(deploy.subprocess, "run", return_value=SimpleNamespace(stdout=f"{latest}\trefs/heads/main\n")),
            patch.object(deploy.Path, "read_bytes", return_value=b'{"ref":"refs/heads/main"}'),
            patch.object(deploy, "urlopen", return_value=response, side_effect=error) as send,
            redirect_stdout(output),
        ):
            result = deploy.main()
        self.assertNotIn("test-secret", output.getvalue())
        return result, send

    def test_forward_push_payload_after_success(self):
        result, send = self.run_script()
        self.assertEqual(result, 0)
        request = send.call_args.args[0]
        self.assertEqual(request.data, b'{"ref":"refs/heads/main"}')
        self.assertEqual(request.get_header("X-github-event"), "push")

    def test_cloudflare_html_is_failure_even_with_200(self):
        result, _ = self.run_script(body=b"<title>Sign in Cloudflare Access</title>", content_type="text/html")
        self.assertEqual(result, 1)

    def test_unexpected_json_does_not_count_as_deployment(self):
        result, _ = self.run_script(body=b'{"message":"Ping received"}')
        self.assertEqual(result, 1)

    def test_network_error_does_not_print_secret(self):
        result, _ = self.run_script(error=URLError("https://example.com/api/deploy/test-secret"))
        self.assertEqual(result, 1)

    def test_newer_commit_skips_old_run(self):
        result, send = self.run_script(latest="newer")
        self.assertEqual(result, 0)
        send.assert_not_called()

    def test_missing_secret_skips_deploy(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(deploy, "urlopen") as send, redirect_stdout(io.StringIO()):
            self.assertEqual(deploy.main(), 0)
        send.assert_not_called()
