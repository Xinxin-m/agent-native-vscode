import contextlib,io,types,unittest
from unittest.mock import patch
import session_reference,sessions_cli
class ReferenceTests(unittest.TestCase):
 def test_exact_cli_output_and_quoted_resume_command(self):
  key='claude-12345678-abcd-4000-8000-123456789012'
  ctx=types.SimpleNamespace(index={key:{}},runs_by_key={},live={},title=lambda k:'Title | with\nspacing',transcript=lambda k:('/tmp/a b/session.jsonl','mac'),cwd_of=lambda k:"/tmp/a b's folder",host_of=lambda k:'mac',account_of=lambda k:'claude-one')
  args=types.SimpleNamespace(key=key,json=False)
  out=io.StringIO()
  with patch.object(sessions_cli,'Context',return_value=ctx),contextlib.redirect_stdout(out):sessions_cli.cmd_ref(args)
  result=session_reference.payload(key,ctx)
  self.assertEqual(result['reference'].encode(),out.getvalue().encode())
  self.assertEqual(result['session_id'],key[7:]);self.assertEqual(result['transcript_path'],'/tmp/a b/session.jsonl')
  self.assertIn("'\"'\"'",result['resume_command']);self.assertTrue(result['reference'].endswith('\n'))
 def test_invalid_key_cannot_be_a_shell_fragment(self):
  for key in ('', '$(whoami)','claude-x;rm','unknown-x'):
   with self.assertRaises(ValueError):session_reference.payload(key)
if __name__=='__main__':unittest.main()
