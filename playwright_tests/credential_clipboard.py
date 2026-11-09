from playwright.sync_api import Page, expect


def install_credential_clipboard(page: Page) -> None:
    """Use a deterministic in-memory clipboard for credential-copy browser tests."""

    page.add_init_script(
        """
        (() => {
          const state = { writes: [] };
          Object.defineProperty(window, "__credentialClipboard", {
            configurable: false,
            value: state,
          });
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: {
              writeText: async (text) => {
                state.writes.push(text);
              },
            },
          });
        })();
        """
    )


def assert_credential_was_copied(page: Page) -> None:
    status = page.locator("#copy-status")
    expect(status).to_be_visible()
    expect(status).to_have_text("Credential copied")
    assert page.evaluate(
        """() => {
          const token = document.querySelector("#one-time-token")?.textContent || "";
          const writes = window.__credentialClipboard?.writes || [];
          return Boolean(token && writes.length === 1 && writes[0] === token);
        }"""
    )
