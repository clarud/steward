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


def test_telegram_presenter_splits_long_escaped_text_without_repeating_actions() -> None:
    response = PresentedReply(
        ("<evidence> " * 800),
        (ReplyAction("Accept record", "/approve_action 1"),),
        title="Long result",
    )

    rendered = TelegramPresenter().render_many(response)

    assert len(rendered) > 1
    assert all(len(item.text) <= 3_800 for item in rendered)
    assert all(item.rows == () for item in rendered[:-1])
    assert rendered[-1].rows[0][0].label == "Accept"
    assert "&lt;evidence&gt;" in "".join(item.text for item in rendered)
