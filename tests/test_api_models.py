import pytest
from pydantic import ValidationError

from web.api_models import SearchKeywordRequest, SearchRecentRequest


def test_search_recent_limit_is_bounded():
    with pytest.raises(ValidationError):
        SearchRecentRequest(limit=1000)


def test_search_keyword_requires_text():
    with pytest.raises(ValidationError):
        SearchKeywordRequest(keyword="")
