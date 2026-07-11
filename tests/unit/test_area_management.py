"""Test area (domain) CRUD operations: add_area, update_area, delete_area.

Mirrors the validation/mocking pattern used in test_delete_validation.py.
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
        'output': 'AREA-ID-123'
    })
    return mock


@pytest.fixture
def tools(mock_applescript):
    """Create ThingsTools with mocked AppleScript."""
    return ThingsTools(mock_applescript)


@pytest.mark.asyncio
class TestAddArea:
    """Test add_area."""

    async def test_add_area_succeeds(self, tools, mock_applescript):
        result = await tools.add_area(title="New Area")

        assert result['success'] is True
        assert result['area_id'] == 'AREA-ID-123'

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'make new area' in script
        assert 'New Area' in script

    async def test_add_area_with_tags(self, tools, mock_applescript):
        result = await tools.add_area(title="New Area", tags=["work", "home"])

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'tag names of newArea' in script
        assert 'work, home' in script

    async def test_add_area_handles_applescript_error(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'error: Things got an error'
        }

        result = await tools.add_area(title="New Area")

        assert result['success'] is False


@pytest.mark.asyncio
class TestUpdateArea:
    """Test update_area."""

    async def test_update_with_none_fails(self, tools):
        result = await tools.update_area(None, title="Renamed")

        assert result['success'] is False
        assert result.get('field') == 'area_id'

    async def test_update_with_empty_string_fails(self, tools):
        result = await tools.update_area('', title="Renamed")

        assert result['success'] is False
        assert result.get('field') == 'area_id'

    async def test_update_with_whitespace_only_fails(self, tools):
        result = await tools.update_area('   ', title="Renamed")

        assert result['success'] is False

    async def test_update_with_valid_id_succeeds(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'updated'
        }

        result = await tools.update_area('ValidAreaID123', title="Renamed Area")

        assert result['success'] is True

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'ValidAreaID123' in script
        assert 'Renamed Area' in script

    async def test_update_with_tags(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'updated'
        }

        result = await tools.update_area('ValidAreaID123', tags=["errand"])

        assert result['success'] is True
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'tag names of targetArea' in script

    async def test_update_error_handling(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': False,
            'error': 'Can\'t get area id "NonexistentID"'
        }

        result = await tools.update_area('NonexistentID', title="X")

        assert result['success'] is False


@pytest.mark.asyncio
class TestDeleteArea:
    """Test delete_area."""

    async def test_delete_with_none_fails(self, tools):
        result = await tools.delete_area(None)

        assert result['success'] is False
        assert result.get('field') == 'area_id'

    async def test_delete_with_empty_string_fails(self, tools):
        result = await tools.delete_area('')

        assert result['success'] is False
        assert result.get('field') == 'area_id'

    async def test_delete_with_whitespace_only_fails(self, tools):
        result = await tools.delete_area('   ')

        assert result['success'] is False

    async def test_delete_with_valid_id_succeeds(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': True,
            'output': 'deleted'
        }

        result = await tools.delete_area('ValidAreaID123')

        assert result['success'] is True
        assert 'successfully' in result['message'].lower()

        mock_applescript.execute_applescript.assert_called_once()
        script = mock_applescript.execute_applescript.call_args[0][0]
        assert 'ValidAreaID123' in script
        assert 'delete' in script.lower()

    async def test_delete_error_handling(self, tools, mock_applescript):
        mock_applescript.execute_applescript.return_value = {
            'success': False,
            'error': 'Can\'t get area id "NonexistentID"'
        }

        result = await tools.delete_area('NonexistentID')

        assert result['success'] is False
