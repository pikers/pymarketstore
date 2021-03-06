from __future__ import absolute_import

import logging
import re

import numpy as np
import pandas as pd
import requests
import six

from .jsonrpc import JsonRpcClient, MsgpackRpcClient
from .results import QueryReply
from .stream import StreamConn

logger = logging.getLogger(__name__)

data_type_conv = {
    '<f4': 'f',
    '<f8': 'd',
    '<i4': 'i',
    '<i8': 'q',
}


def isiterable(something):
    """
    check if something is a list, tuple or set
    :param something: any object
    :return: bool. true if something is a list, tuple or set
    """
    return isinstance(something, (list, tuple, set))


def get_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, np.integer)):
        return pd.Timestamp(value, unit='s')
    return pd.Timestamp(value)


class Params(object):

    def __init__(self, symbols, timeframe, attrgroup,
                 start=None, end=None,
                 limit=None, limit_from_start=None):
        if not isiterable(symbols):
            symbols = [symbols]
        self.tbk = ','.join(symbols) + "/" + timeframe + "/" + attrgroup
        self.key_category = None  # server default
        self.start = get_timestamp(start)
        self.end = get_timestamp(end)
        self.limit = limit
        self.limit_from_start = limit_from_start
        self.functions = None

    def set(self, key, val):
        if not hasattr(self, key):
            raise AttributeError()
        if key in ('start', 'end'):
            setattr(self, key, get_timestamp(val))
        else:
            setattr(self, key, val)
        return self

    def __repr__(self):
        content = ('tbk={}, start={}, end={}, '.format(
            self.tbk, self.start, self.end,
        ) +
                   'limit={}, '.format(self.limit) +
                   'limit_from_start={}'.format(self.limit_from_start))
        return 'Params({})'.format(content)


class Client(object):

    def __init__(self, endpoint='http://localhost:5993/rpc'):
        """
        initialize the MarketStore client with specified server endpoint
        :param endpoint:
        """
        self.endpoint = endpoint
        rpc_client = self._get_rpc_client('msgpack')
        self.rpc = rpc_client(self.endpoint)

    @staticmethod
    def _get_rpc_client(codec='msgpack'):
        """
        get an RPC client in specified codec
        :param codec: currently supporting 'msgpack' only
        :return: RPC client
        """
        if codec == 'msgpack':
            return MsgpackRpcClient
        return JsonRpcClient

    def _request(self, method, **query):
        """
        execute a request to MarketStore server
        :param method: method name in string (ex. 'DataService.Query', 'DataService.ListSymbols')
        :param query:
        :return:
        """
        try:
            return self.rpc.call(method, **query)
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def query(self, params):
        """
        execute QUERY to MarketStore server
        :param params: Params object used to query
        :return: QueryReply object
        """

        query = self._build_query(params)
        reply = self._request('DataService.Query', **query)

        return QueryReply(reply)

    def write(self, recarray, tbk, isvariablelength=False):
        """
        execute WRITE to MarketStore server
        :param recarray: numpy.array object to write
        :param tbk: Time Bucket Key string.
        ('{symbol name}/{time frame}/{attribute group name}' ex. 'TSLA/1Min/OHLCV' , 'AAPL/1Min/TICK' )
        :param isvariablelength: should be set true if the record content is variable-length array
        :return:
        """
        data = self._build_data(recarray, tbk)

        write_request = {}
        write_request['dataset'] = data
        write_request['is_variable_length'] = isvariablelength
        writer = {}
        writer['requests'] = [write_request]
        try:
            return self.rpc.call("DataService.Write", **writer)
        except requests.exceptions.ConnectionError:
            raise requests.exceptions.ConnectionError(
                "Could not contact server")

    @staticmethod
    def _build_data(recarray, tbk):
        """
        build data for write
        :param recarray: numpy.array object to write
        :param tbk: Time Bucket Key string.
        :return: data in dictionary
        """
        data = {}
        data['types'] = [
            recarray.dtype[name].str.replace('<', '')
            for name in recarray.dtype.names
        ]
        data['names'] = recarray.dtype.names
        data['data'] = []
        for name in recarray.dtype.names:
            data['data'].append(bytes(buffer(recarray[name])) if six.PY2
                                else bytes(memoryview(recarray[name])))
        data['length'] = len(recarray)
        data['startindex'] = {tbk: 0}
        data['lengths'] = {tbk: len(recarray)}

        return data

    @staticmethod
    def _build_query(params):
        """
        build parameters for QUERY
        :param params: pymarketstore.Params object
        :return: request params in array
        """
        reqs = []
        if not isiterable(params):
            params = [params]
        for param in params:
            req = {
                'destination': param.tbk,
            }
            if param.key_category is not None:
                req['key_category'] = param.key_category
            if param.start is not None:
                req['epoch_start'] = int(param.start.value / (10 ** 9))
            if param.end is not None:
                req['epoch_end'] = int(param.end.value / (10 ** 9))
            if param.limit is not None:
                req['limit_record_count'] = int(param.limit)
            if param.limit_from_start is not None:
                req['limit_from_start'] = bool(param.limit_from_start)
            if param.functions is not None:
                req['functions'] = param.functions
            reqs.append(req)
        return {
            'requests': reqs,
        }

    def list_symbols(self):
        """
        execute LIST SYMBOLS to MarketStore server
        :return:
        """
        reply = self._request('DataService.ListSymbols')
        if 'Results' in reply.keys():
            return reply['Results']
        return []

    def destroy(self, tbk):
        """
        Delete a bucket
        :param tbk: Time Bucket Key Name (i.e. "TEST/1Min/Tick" )
        :return: reply object
        """
        destroy_req = {'requests': [{'key': tbk}]}
        reply = self._request('DataService.Destroy', **destroy_req)
        return reply

    def server_version(self):
        """
        get MarketStore server version in the 'Marketstore-Version' HTTP response headers.
        :return: version string
        """
        resp = requests.head(self.endpoint)
        return resp.headers.get('Marketstore-Version')

    def stream(self):
        endpoint = re.sub('^http', 'ws',
                          re.sub(r'/rpc$', '/ws', self.endpoint))
        return StreamConn(endpoint)

    def __repr__(self):
        return 'Client("{}")'.format(self.endpoint)
