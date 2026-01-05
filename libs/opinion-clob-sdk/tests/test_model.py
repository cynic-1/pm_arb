import pytest
from opinion_clob_sdk.model import TopicStatus, TopicType, TopicStatusFilter, TopicSortType


class TestEnums:
    """Test enum values match expected API values"""

    def test_topic_status_values(self):
        """Test TopicStatus enum values"""
        assert TopicStatus.CREATED.value == 1
        assert TopicStatus.ACTIVATED.value == 2
        assert TopicStatus.RESOLVING.value == 3
        assert TopicStatus.RESOLVED.value == 4
        assert TopicStatus.FAILED.value == 5
        assert TopicStatus.DELETED.value == 6

    def test_topic_type_values(self):
        """Test TopicType enum values"""
        assert TopicType.BINARY.value == 0
        assert TopicType.CATEGORICAL.value == 1

    def test_topic_status_filter_values(self):
        """Test TopicStatusFilter enum values"""
        assert TopicStatusFilter.ALL.value is None  # Changed to None
        assert TopicStatusFilter.ACTIVATED.value == "activated"  # Changed to string
        assert TopicStatusFilter.RESOLVED.value == "resolved"  # Changed to string

    def test_topic_sort_type_values(self):
        """Test TopicSortType enum values"""
        assert TopicSortType.NO_SORT.value == 0
        assert TopicSortType.BY_TIME_DESC.value == 1
        assert TopicSortType.BY_CUTOFF_TIME_ASC.value == 2
        assert TopicSortType.BY_VOLUME_DESC.value == 3
        assert TopicSortType.BY_VOLUME_ASC.value == 4
        assert TopicSortType.BY_VOLUME_24H_DESC.value == 5
        assert TopicSortType.BY_VOLUME_24H_ASC.value == 6
        assert TopicSortType.BY_VOLUME_7D_DESC.value == 7
        assert TopicSortType.BY_VOLUME_7D_ASC.value == 8

    def test_enum_membership(self):
        """Test enum membership"""
        assert TopicStatus.ACTIVATED in TopicStatus
        assert TopicType.BINARY in TopicType
        assert TopicStatusFilter.ALL in TopicStatusFilter
        assert TopicSortType.BY_VOLUME_DESC in TopicSortType

    def test_enum_iteration(self):
        """Test enum iteration"""
        status_values = [status.value for status in TopicStatus]
        assert 1 in status_values
        assert 2 in status_values
        assert 4 in status_values

        type_values = [t.value for t in TopicType]
        assert 0 in type_values
        assert 1 in type_values

        sort_values = [s.value for s in TopicSortType]
        assert 0 in sort_values
        assert 1 in sort_values
        assert 3 in sort_values
        assert 8 in sort_values
