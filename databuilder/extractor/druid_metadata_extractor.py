import logging
from collections import namedtuple
import textwrap

from pyhocon import ConfigFactory, ConfigTree  # noqa: F401
from typing import Iterator, Union, Dict, Any  # noqa: F401

from databuilder import Scoped
from databuilder.extractor.base_extractor import Extractor
from databuilder.extractor.sql_alchemy_extractor import SQLAlchemyExtractor
from databuilder.models.table_metadata import TableMetadata, ColumnMetadata
from itertools import groupby


TableKey = namedtuple('TableKey', ['schema', 'table_name'])

LOGGER = logging.getLogger(__name__)


class DruidMetadataExtractor(Extractor):
    """
    Extracts Druid table and column metadata from druid using dbapi extractor
    """
    SQL_STATEMENT = textwrap.dedent("""
        SELECT
        TABLE_SCHEMA as schema,
        TABLE_NAME as name,
        COLUMN_NAME as col_name,
        DATA_TYPE as col_type,
        ORDINAL_POSITION as col_sort_order
        FROM INFORMATION_SCHEMA.COLUMNS
        {where_clause_suffix}
        order by TABLE_SCHEMA, TABLE_NAME, CAST(ORDINAL_POSITION AS int)
    """)

    # CONFIG KEYS
    WHERE_CLAUSE_SUFFIX_KEY = 'where_clause_suffix'
    CLUSTER_KEY = 'cluster'

    DEFAULT_CONFIG = ConfigFactory.from_dict({WHERE_CLAUSE_SUFFIX_KEY: ' ',
                                              CLUSTER_KEY: 'gold'})

    def init(self, conf):
        # type: (ConfigTree) -> None
        conf = conf.with_fallback(DruidMetadataExtractor.DEFAULT_CONFIG)
        self._cluster = '{}'.format(conf.get_string(DruidMetadataExtractor.CLUSTER_KEY))

        self.sql_stmt = DruidMetadataExtractor.SQL_STATEMENT.format(
            where_clause_suffix=conf.get_string(DruidMetadataExtractor.WHERE_CLAUSE_SUFFIX_KEY,
                                                default=''))

        self._alchemy_extractor = SQLAlchemyExtractor()
        sql_alch_conf = Scoped.get_scoped_conf(conf, self._alchemy_extractor.get_scope())\
            .with_fallback(ConfigFactory.from_dict({SQLAlchemyExtractor.EXTRACT_SQL: self.sql_stmt}))

        self._alchemy_extractor.init(sql_alch_conf)
        self._extract_iter = None  # type: Union[None, Iterator]

    def extract(self):
        # type: () -> Union[TableMetadata, None]
        if not self._extract_iter:
            self._extract_iter = self._get_extract_iter()
        try:
            return next(self._extract_iter)
        except StopIteration:
            return None

    def get_scope(self):
        # type: () -> str
        return 'extractor.druid_metadata'

    def _get_extract_iter(self):
        # type: () -> Iterator[TableMetadata]
        """
        Using itertools.groupby and raw level iterator, it groups to table and yields TableMetadata
        :return:
        """
        for key, group in groupby(self._get_raw_extract_iter(), self._get_table_key):
            columns = []
            # no table description and column description
            for row in group:
                last_row = row
                columns.append(ColumnMetadata(name=row['col_name'],
                                              description='',
                                              col_type=row['col_type'],
                                              sort_order=row['col_sort_order']))
            yield TableMetadata(database='druid',
                                cluster=self._cluster,
                                schema=last_row['schema'],
                                name=last_row['name'],
                                description='',
                                columns=columns)

    def _get_raw_extract_iter(self):
        # type: () -> Iterator[Dict[str, Any]]
        """
        Provides iterator of result row from dbapi extractor
        :return:
        """
        row = self._alchemy_extractor.extract()
        while row:
            yield row
            row = self._alchemy_extractor.extract()

    def _get_table_key(self, row):
        # type: (Dict[str, Any]) -> Union[TableKey, None]
        """
        Table key consists of schema and table name
        :param row:
        :return:
        """
        if row:
            return TableKey(schema=row['schema'], table_name=row['name'])

        return None
