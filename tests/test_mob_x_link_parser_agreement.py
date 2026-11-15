from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from agent.brain.mob_x_link_processor import parse_official_link

ROOT = Path(__file__).resolve().parents[1]
POST_ID = "2107878755079213476"
URL_CASES = [
    f"https://x.com/mobgamesstudios/status/{POST_ID}",
    f"https://twitter.com/mobgamesstudios/status/{POST_ID}?s=19",
    f"https://www.x.com/mobgamesstudios/status/{POST_ID}/?a=share&s=19#copy",
    f"https://mobile.twitter.com/mobgamesstudios/status/{POST_ID}#post",
    f"http://x.com/mobgamesstudios/status/{POST_ID}",
    f"https://x.com/someoneelse/status/{POST_ID}?s=19",
    f"https://x.com.evil.example/mobgamesstudios/status/{POST_ID}",
    f"https://x.com/anything/../mobgamesstudios/status/{POST_ID}",
    f"https://evil@x.com/mobgamesstudios/status/{POST_ID}",
    f"https://x.com:443/mobgamesstudios/status/{POST_ID}",
    f"https://x.com/mobgamesstudios/status/{POST_ID[5:]}",
    f"javascript:https://x.com/mobgamesstudios/status/{POST_ID}",
    f"https://x.com/mobgamesstudios/status/{POST_ID}/extra",
]


class MobXLinkParserAgreementTests(unittest.TestCase):
    def test_javascript_and_python_parsers_agree_on_tracking_and_rejections(self):
        script = """
import { normalizeMobXLink } from './src/mob_x_link.js';
const urls = JSON.parse(process.argv[1]);
const results = urls.map(url => {
  const parsed = normalizeMobXLink(url);
  return parsed ? [parsed.canonicalUrl, parsed.externalId] : null;
});
process.stdout.write(JSON.stringify(results));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script, json.dumps(URL_CASES)],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=20,
        )
        js_results = json.loads(completed.stdout)
        python_results = []
        for url in URL_CASES:
            parsed = parse_official_link(url)
            python_results.append(list(parsed) if parsed else None)
        self.assertEqual(js_results, python_results)

    def test_parsers_drop_query_and_fragment_to_the_canonical_url(self):
        expected = f"https://x.com/mobgamesstudios/status/{POST_ID}"
        for url in (
            f"https://x.com/mobgamesstudios/status/{POST_ID}?s=19",
            f"https://twitter.com/mobgamesstudios/status/{POST_ID}?a=1&s=19#share",
        ):
            with self.subTest(url=url):
                self.assertEqual(parse_official_link(url), (expected, POST_ID))


if __name__ == "__main__":
    unittest.main()
