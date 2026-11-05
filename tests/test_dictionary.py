"""Synthetic official-API-shaped fixtures; these are not real dictionary evidence."""
import os
import unittest
from unittest.mock import patch

import httpx

from backend.dictionary import (DictionaryClient, DictionaryError, long_positions,
                                parse_detail, parse_search, pronunciation_for)


# Deliberately fictional target/sense identifiers for parser regression only.
DETAIL = """<channel><item><target_code>999999</target_code><word_info>
<word>가-상</word><original_language_info><original_language>假想</original_language></original_language_info>
<pronunciation_info><pronunciation>가ː상</pronunciation></pronunciation_info>
<pos_info><pos>명사</pos><comm_pattern_info><sense_info><sense_code>1</sense_code>
<definition>시험용 의미 하나.</definition></sense_info><sense_info><sense_code>2</sense_code>
<definition>시험용 의미 둘.</definition></sense_info></comm_pattern_info></pos_info>
<conju_info><conjugation_info><conjugation>가상해</conjugation>
<pronunciation_info><pronunciation>가상해</pronunciation></pronunciation_info>
</conjugation_info></conju_info>
</word_info></item></channel>""".encode()
SEARCH = b"<channel><total>2</total><item><target_code>999999</target_code></item><item><target_code>999998</target_code></item></channel>"


class DictionaryParsingTests(unittest.TestCase):
    def test_all_homonym_ids_and_distinct_senses_are_retained(self):
        self.assertEqual(parse_search(SEARCH), ["999999", "999998"])
        candidates = parse_detail(DETAIL, "999999")
        self.assertEqual(len(candidates), 2)
        self.assertEqual({item["sense_code"] for item in candidates}, {"1", "2"})
        self.assertEqual(candidates[0]["word"], "가상")
        self.assertEqual(candidates[0]["origin"], "假想")
        self.assertEqual(candidates[0]["pronunciations"], ["가ː상"])
        self.assertIn("word_no=999999", candidates[0]["url"])
        self.assertTrue(candidates[0]["queried_at"])

    def test_actual_inflection_required_and_base_length_not_copied(self):
        candidate = parse_detail(DETAIL, "999999")[0]
        base = pronunciation_for(candidate, "가상")
        actual = pronunciation_for(candidate, "가상해")
        self.assertEqual(long_positions(base[0]), [0])
        self.assertEqual(long_positions(actual[0]), [])
        self.assertIsNone(pronunciation_for(candidate, "가상하면"))
        # Explicit exception-length evidence stays long when it is actually present.
        candidate["inflections"].append({"form": "가상해서", "pronunciations": ["가ː상해서"]})
        self.assertEqual(long_positions(pronunciation_for(candidate, "가상해서")[0]), [0])

    def test_missing_or_disagreeing_readings_are_not_short(self):
        self.assertIsNone(long_positions([]))
        self.assertIsNone(long_positions(["가ː상", "가상"]))
        self.assertIsNone(long_positions(["? unparsed"]))
        self.assertEqual(long_positions(["가:상"]), [0])
        self.assertEqual(long_positions(["가상"]), [])
        self.assertEqual(long_positions(["가ː상ː"]), [0, 1])

    def test_parse_errors_and_denial_do_not_become_empty_search_results(self):
        for raw in [b"<html>service unavailable</html>", b"garbled", b"<error><message>bad key</message></error>",
                    b'<!DOCTYPE x [<!ENTITY fake "bad">]><channel><total>0</total></channel>']:
            with self.subTest(raw=raw), self.assertRaises(DictionaryError):
                parse_search(raw)
        self.assertEqual(parse_search(b"<channel><total>0</total></channel>"), [])
        with self.assertRaises(DictionaryError):
            parse_detail(b"<channel><item><word_info/></item></channel>", "999999")


class DictionaryTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_read_retries_once_but_denial_does_not(self):
        class TemporaryFailure:
            def __init__(self, code):
                self.calls = 0
                self.code = code

            async def get(self, url, params):
                self.calls += 1
                return httpx.Response(self.code if self.calls == 1 else 200,
                                      request=httpx.Request("GET", url))

        transient = TemporaryFailure(503)
        result = await DictionaryClient()._get(transient, "https://stdict.korean.go.kr/api/search.do", {})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(transient.calls, 2)
        denied = TemporaryFailure(403)
        with self.assertRaises(httpx.HTTPStatusError):
            await DictionaryClient()._get(denied, "https://stdict.korean.go.kr/api/search.do", {})
        self.assertEqual(denied.calls, 1)

    async def test_timeout_retries_once(self):
        class TemporaryTimeout:
            calls = 0

            async def get(self, url, params):
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("temporary", request=httpx.Request("GET", url))
                return httpx.Response(200, request=httpx.Request("GET", url))

        client = TemporaryTimeout()
        await DictionaryClient()._get(client, "https://stdict.korean.go.kr/api/search.do", {})
        self.assertEqual(client.calls, 2)

    async def test_search_detail_and_cache_are_used(self):
        requests = []

        class FakeHTTP:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, params):
                requests.append((url, params))
                body = b"<channel><total>1</total><item><target_code>999999</target_code></item></channel>" if url.endswith("search.do") else DETAIL
                return httpx.Response(200, content=body, request=httpx.Request("GET", url))

        with patch.dict(os.environ, {"STDICT_API_KEY": "synthetic-fixture-key"}), patch("backend.dictionary.httpx.AsyncClient", FakeHTTP):
            client = DictionaryClient()
            found = await client.lookup("가상")
            again = await client.lookup("가상")
        self.assertEqual(found, again)
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0][0].endswith("search.do"))
        self.assertTrue(requests[1][0].endswith("view.do"))
        self.assertEqual(requests[1][1]["method"], "target_code")
        self.assertEqual(requests[1][1]["q"], "999999")
        self.assertEqual(len(found), 2)

    async def test_missing_key_is_a_typed_failure(self):
        with patch.dict(os.environ, {"STDICT_API_KEY": ""}):
            with self.assertRaises(DictionaryError):
                await DictionaryClient().lookup("가상")

    async def test_http_failure_hides_credential_url(self):
        class FailingHTTP:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def get(self, url, params):
                request = httpx.Request("GET", url, params=params)
                raise httpx.HTTPStatusError("error with sensitive query", request=request,
                                            response=httpx.Response(403, request=request))

        with patch.dict(os.environ, {"STDICT_API_KEY": "synthetic-sensitive-value"}), patch("backend.dictionary.httpx.AsyncClient", FailingHTTP):
            with self.assertRaises(DictionaryError) as error:
                await DictionaryClient().lookup("가상")
        self.assertNotIn("synthetic-sensitive-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
