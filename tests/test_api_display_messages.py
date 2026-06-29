from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.api_server import _message_display_attachments, _message_display_text


class ApiDisplayMessageTests(unittest.TestCase):
    def test_user_message_prefers_display_metadata(self) -> None:
        message = SimpleNamespace(
            id=10,
            role="user",
            text="Question\n\n[Attachment 1: huge.csv, extension=.csv, media_type=text/csv, source=inline_text]\nrows...",
            metadata={
                "display_text": "Question",
                "attachments": [
                    {
                        "id": "att_1",
                        "name": "huge.csv",
                        "size": 42,
                        "kind": "spreadsheet",
                    }
                ],
            },
        )

        self.assertEqual(_message_display_text(message), "Question")
        self.assertEqual(_message_display_attachments(message)[0]["name"], "huge.csv")

    def test_historic_attachment_text_is_compacted(self) -> None:
        message = SimpleNamespace(
            id=11,
            role="user",
            text=(
                "Please analyze this.\n\n"
                "[Attachment 1: model_guide.docx, extension=.docx, media_type=application/vnd.openxmlformats-officedocument.wordprocessingml.document, source=inline_base64]\n"
                "Very long extracted document body..."
            ),
            metadata={},
        )

        self.assertEqual(_message_display_text(message), "Please analyze this.")
        attachment = _message_display_attachments(message)[0]
        self.assertEqual(attachment["name"], "model_guide.docx")
        self.assertEqual(attachment["kind"], "document")


if __name__ == "__main__":
    unittest.main()
