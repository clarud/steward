from steward.presentation import PresentedReply, ReplyAction
from steward.telegram.presentation import TelegramPresenter


def test_telegram_presenter_escapes_dynamic_html_and_compacts_buttons() -> None:
    rendered = TelegramPresenter().render(
        PresentedReply(
            "<untrusted filename>.pdf is staged.",
            (
                ReplyAction("Allow external model", "/external"),
                ReplyAction("Keep this reviewed note", "/keep"),
                ReplyAction("Import an extremely long filename from Drive", "/import"),
            ),
            title="Review <document>",
            icon="📄",
        )
    )

    assert rendered.text == "📄 <b>Review &lt;document&gt;</b>\n\n&lt;untrusted filename&gt;.pdf is staged."
    assert [[action.label for action in row] for row in rendered.rows] == [
        ["Use external", "Keep"], ["Import"],
    ]
