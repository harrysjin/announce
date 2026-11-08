"""Synthetic API-shaped checks for auxiliary search; no lexical gold data."""
import os
import unittest
from unittest.mock import patch

import httpx

from backend.dictionary import DictionaryClient, DictionaryError, pronunciation_for


SEARCH = b"<channel><total>1</total><item><target_code>999997</target_code><word>synthetic</word></item></channel>"
EMPTY = b"<channel><total>0</total></channel>"
DETAIL = """<channel><item><target_code>999997</target_code><word_info>
<word>가상하다</word><pronunciation_info><pronunciation>가ː상하다</pronunciation></pronunciation_info>
<conju_info><conjugation_info><conjugation>가상하여</conjugation>
<pronunciation_info><pronunciation>가상하여</pronunciation></pronunciation_info>
</conjugation_info></conju_info>
<pos_info><pos>동사</pos><sense_info><sense_code>1</sense_code>
<definition>사전 검색 계약 검증용 가상 의미.</definition></sense_info></pos_info>
</word_info></item></channel>""".encode()


class AuxiliarySearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_auxiliary_search_fetches_detail_and_has_a_separate_cache(self):
        requests = []
        options = []

        class FakeHTTP:
            def __init__(self, **kwargs): options.append(kwargs)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def get(self, url, params):
                requests.append((url, params.copy()))
                if url.endswith("search.do"):
                    body = SEARCH if params.get("target") == 5 else EMPTY
                else:
                    body = DETAIL
                return httpx.Response(200, content=body, request=httpx.Request("GET", url))

        with patch.dict(os.environ, {"STDICT_API_KEY": "synthetic-key"}), patch("backend.dictionary.httpx.AsyncClient", FakeHTTP):
            client = DictionaryClient()
            self.assertEqual(await client.lookup("가상하여"), [])
            found = await client.lookup_inflections("가상하여")
            self.assertEqual(await client.lookup("가상하여"), [])
            self.assertEqual(await client.lookup_inflections("가상하여"), found)

        self.assertEqual(len(requests), 3)
        self.assertNotIn("target", requests[0][1])
        self.assertEqual(requests[1][1]["target"], 5)
        self.assertEqual(requests[1][1]["advanced"], "y")
        self.assertEqual(requests[1][1]["method"], "exact")
        self.assertEqual(requests[2][1]["method"], "target_code")
        self.assertEqual(found[0]["lookup_provenance"], {"type": "inflection_search", "query": "가상하여", "target": 5})
        self.assertEqual(found[0]["pos"], "동사")
        self.assertTrue(found[0]["definition"])
        self.assertTrue(found[0]["queried_at"])
        self.assertIn("word_no=999997", found[0]["url"])
        self.assertEqual(pronunciation_for(found[0], "가상하여")[0], ["가상하여"])
        self.assertTrue(all(value["trust_env"] for value in options))
        self.assertTrue(all(not value["follow_redirects"] for value in options))

    async def test_search_hit_does_not_create_unlisted_form_pronunciation(self):
        class FakeHTTP:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def get(self, url, params):
                return httpx.Response(200, content=SEARCH if url.endswith("search.do") else DETAIL,
                                      request=httpx.Request("GET", url))

        with patch.dict(os.environ, {"STDICT_API_KEY": "synthetic-key"}), patch("backend.dictionary.httpx.AsyncClient", FakeHTTP):
            client = DictionaryClient()
            direct = await client.lookup("가상하다")
            found = await client.lookup_inflections("가상하며")
        self.assertEqual(direct[0]["lookup_provenance"], {"type": "exact", "query": "가상하다", "target": 1})
        self.assertIsNone(pronunciation_for(found[0], "가상하며"))

    async def test_auxiliary_missing_key_and_transport_failure_are_typed_and_private(self):
        with patch.dict(os.environ, {"STDICT_API_KEY": ""}):
            with self.assertRaises(DictionaryError):
                await DictionaryClient().lookup_inflections("가상하여")

        class FailingHTTP:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def get(self, url, params):
                request = httpx.Request("GET", url, params=params)
                raise httpx.HTTPStatusError("sensitive query", request=request,
                                            response=httpx.Response(403, request=request))

        with patch.dict(os.environ, {"STDICT_API_KEY": "synthetic-sensitive-key"}), patch("backend.dictionary.httpx.AsyncClient", FailingHTTP):
            client = DictionaryClient()
            with self.assertRaises(DictionaryError) as error:
                await client.lookup_inflections("가상하여")
            self.assertEqual(client.inflection_cache, {})
        self.assertNotIn("synthetic-sensitive-key", str(error.exception))


if __name__ == "__main__":
    unittest.main()
