"""Deployment diagnostics must be read-only and must not expose credentials."""
import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.learning_setup import check_setup


class LearningSetupTests(unittest.TestCase):
    def test_missing_database_does_not_attempt_a_connection(self):
        with patch.dict(os.environ, {'DATABASE_URL': '', 'LEARNING_ADMIN_TOKEN': '', 'LEARNING_DOCUMENT_KEY': '', 'LEARNING_PUBLIC_ORIGIN': ''}), \
             patch('backend.learning_db.connection', side_effect=AssertionError('must not connect')):
            report = check_setup()
        self.assertFalse(report['ready'])
        self.assertTrue(report['read_only'])
        self.assertFalse(report['auto_confirmation_enabled'])

    def test_driver_or_http_error_cannot_leak_the_dsn(self):
        private_dsn = 'postgresql://private-user:private-password@private-db.example/private'
        with patch.dict(os.environ, {'DATABASE_URL': private_dsn}), \
             patch('backend.learning_db.connection', side_effect=HTTPException(503, private_dsn)):
            report = check_setup()
        self.assertFalse(report['ready'])
        self.assertNotIn('private-password', json.dumps(report))
        self.assertNotIn('private-db.example', json.dumps(report))

    def test_reused_secrets_are_reported_without_printing_the_values(self):
        secret = 'fixture-secret-' * 4
        with patch.dict(os.environ, {'DATABASE_URL': '', 'LEARNING_ADMIN_TOKEN': secret, 'LEARNING_DOCUMENT_KEY': secret, 'LEARNING_PUBLIC_ORIGIN': 'https://sori.example'}):
            report = check_setup()
        self.assertFalse(next(c for c in report['checks'] if c['id'] == 'separate_keys')['ready'])
        self.assertNotIn(secret, json.dumps(report))

    def test_origin_requires_https_and_no_path_or_embedded_credentials(self):
        for origin in ('http://sori.example', 'https://sori.example/', 'https://sori.example/learning', 'https://user:secret@sori.example'):
            with self.subTest(origin=origin), patch.dict(os.environ, {'DATABASE_URL': '', 'LEARNING_PUBLIC_ORIGIN': origin}):
                report = check_setup()
            self.assertFalse(next(c for c in report['checks'] if c['id'] == 'public_origin')['ready'])


if __name__ == '__main__':
    unittest.main()
