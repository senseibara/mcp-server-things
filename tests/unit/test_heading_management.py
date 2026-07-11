"""Test heading (section within a project) CRUD operations, and the fix for
assigning todos to a heading via add_todo/update_todo.

Mirrors the validation/mocking pattern used in test_area_management.py.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from things_mcp.tools import ThingsTools
from things_mcp.services.applescript_manager import AppleScriptManager


@pytest.fixture
def mock_applescript():
    """Create a mock AppleScript manager."""
    mock = MagicMock(spec=AppleScriptManager)
    mock.execute_applescript = AsyncMock(return_value={
        'success': True,
        'output': 'HEADING-ID-123'
    })
    return mock


@pytest.fixture
def tools(mock_applescript):
    """Create ThingsTools with mocked AppleScript."""
    return ThingsTools(mock_applescript)


@pytest.mark.asyncio
class TestAddHeading:
    """Test add_heading."""

    async def test_add_heading_succeeds(self, tools, mock_applescript):
        result = await tools.add_heading(title="Research", list_id="PROJECT-ID-1")

        assert result['success'] is True
        assert result['heading_id'] == 'HEADING-ID-123'

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'make new heading' in script
        assert 'Research' in script
        assert 'PROJECT-ID-1' in script

    async def test_add_heading_requires_project(self, tools):
        result = await tools.add_heading(title="Research")

        assert result['success'] is False

    async def test_add_heading_handles_applescript_error(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'error: Things got an error'
        }

        result = await tools.add_heading(title="Research", list_id="PROJECT-ID-1")

        assert result['success'] is False


@pytest.mark.asyncio
class TestUpdateHeading:
    """Test update_heading."""

    async def test_update_with_none_fails(self, tools):
        result = await tools.update_heading(None, title="Renamed")

        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_update_with_empty_string_fails(self, tools):
        result = await tools.update_heading('', title="Renamed")

        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_update_requires_title(self, tools):
        result = await tools.update_heading('ValidHeadingID123')

        assert result['success'] is False

    async def test_update_with_valid_id_succeeds(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'updated'
        }

        result = await tools.update_heading('ValidHeadingID123', title="Renamed Heading")

        assert result['success'] is True

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'ValidHeadingID123' in script
        assert 'Renamed Heading' in script

    async def test_update_error_handling(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': False,
            'error': 'Can\'t get heading id "NonexistentID"'
        }

        result = await tools.update_heading('NonexistentID', title="X")

        assert result['success'] is False


@pytest.mark.asyncio
class TestDeleteHeading:
    """Test delete_heading."""

    async def test_delete_with_none_fails(self, tools):
        result = await tools.delete_heading(None)

        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_delete_with_empty_string_fails(self, tools):
        result = await tools.delete_heading('')

        assert result['success'] is False
        assert result.get('field') == 'heading_id'

    async def test_delete_with_valid_id_succeeds(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'deleted'
        }

        result = await tools.delete_heading('ValidHeadingID123')

        assert result['success'] is True
        assert 'successfully' in result['message'].lower()

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'ValidHeadingID123' in script
        assert 'delete' in script.lower()

    async def test_delete_error_handling(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': False,
            'error': 'Can\'t get heading id "NonexistentID"'
        }

        result = await tools.delete_heading('NonexistentID')

        assert result['success'] is False


@pytest.mark.asyncio
class TestAddTodoHeadingAssignment:
    """Regression tests for the add_todo 'heading' bug: previously the
    AppleScript (non-checklist) path silently dropped the heading parameter.
    """

    async def test_add_todo_with_heading_and_list_id_sets_heading(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'TODO-ID-1'
        }

        result = await tools.add_todo(
            title="Research competitors",
            list_id="PROJECT-ID-1",
            heading="Research"
        )

        assert result['success'] is True

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        # This is the core regression check: the heading must actually be
        # set on the newly created to-do, not silently dropped.
        assert 'set heading of newTodo to heading' in script
        assert 'Research' in script
        assert 'PROJECT-ID-1' in script

    async def test_add_todo_with_heading_id_sets_heading_by_id(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'TODO-ID-1'
        }

        result = await tools.add_todo(
            title="Research competitors",
            heading_id="HEADING-ID-9"
        )

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'set heading of newTodo to heading id' in script
        assert 'HEADING-ID-9' in script

    async def test_add_todo_with_heading_but_no_project_fails_clearly(self, tools):
        result = await tools.add_todo(
            title="Research competitors",
            heading="Research"
        )

        assert result['success'] is False
        assert 'heading' in result['error'].lower()

    async def test_add_todo_without_heading_does_not_reference_heading(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'TODO-ID-1'
        }

        result = await tools.add_todo(title="Plain todo", list_id="PROJECT-ID-1")

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'heading' not in script.lower()


@pytest.mark.asyncio
class TestUpdateTodoHeadingAssignment:
    """Tests for moving an existing todo into a heading via update_todo."""

    async def test_update_todo_with_heading_and_list_id_sets_heading(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'updated'
        }

        result = await tools.update_todo(
            todo_id="TODO-ID-1",
            list_id="PROJECT-ID-1",
            heading="Research"
        )

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'set heading of targetTodo to heading' in script
        assert 'Research' in script
        assert 'PROJECT-ID-1' in script

    async def test_update_todo_with_heading_id_sets_heading_by_id(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'updated'
        }

        result = await tools.update_todo(
            todo_id="TODO-ID-1",
            heading_id="HEADING-ID-9"
        )

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'set heading of targetTodo to heading id' in script
        assert 'HEADING-ID-9' in script

    async def test_update_todo_with_heading_but_no_list_id_fails_clearly(self, tools):
        result = await tools.update_todo(
            todo_id="TODO-ID-1",
            heading="Research"
        )

        assert result['success'] is False
        assert 'heading' in result['error'].lower()
