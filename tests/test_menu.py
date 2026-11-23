from tg_admin.__main__ import COMMAND_MENU, HELP_TEXT


def test_menu_lists_only_implemented_commands():
    names = [command.command for command in COMMAND_MENU]
    assert names == ["help", "login", "whoami", "run", "sudo", "approve", "cancel", "logout"]
    assert len(names) == len(set(names))
    assert "sudo" in names
    assert "approve" in names
    assert "docker" not in names


def test_help_describes_available_actions():
    for item in ("/login", "/logout", "/whoami", "/run", "/help", "/menu"):
        assert item in HELP_TEXT
    assert "/run whoami" in HELP_TEXT
    assert "tgexec" in HELP_TEXT
