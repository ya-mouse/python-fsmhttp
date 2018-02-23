# -*- coding: utf-8 -*-
import sys
import socket
import select
import logging
from time import time,sleep
from struct import pack, unpack
from json import dumps as json_encode,loads as json_decode

from fsmsock.proto import TcpTransport

class HttpClient(TcpTransport):
    def __init__(self, host, interval, keepalive=True, port=80):
        self._buf = ''
        self._keepalive = keepalive
        super().__init__(host, interval, (socket.AF_INET, socket.SOCK_STREAM, port))

    def get(self, url, cookies=None):
        self._url = url
        self._data = None
        self._bytes_to_read = -1
        self._buf = bytes("GET {} HTTP/1.1\r\nHost: {}\r\nUser-Agent: m3-collector/1.0\r\n{}{}\r\n".format(
            url,
            self._host,
            "Connection: keep-alive\r\n" if self._keepalive else '',
            "Cookie: {}\r\n".format(cookies) if cookies else ''
        ), 'ascii')

    def post(self, url, data, cookies=None):
        self._url = url
        self._data = None
        self._bytes_to_read = -1
        data = json_encode(data)
        self._buf = bytes("POST {} HTTP/1.1\r\nHost: {}\r\n{}{}Accept: */*\r\nUser-Agent: m3-collector/1.0\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{{\"data\":{}}}\r\n\r\n".format(
            url,
            self._host,
            "Connection: keep-alive\r\n" if self._keepalive else '',
            "Cookie: {}\r\n".format(cookies) if cookies else '',
            len(data)+9,
            data
        ), 'ascii')

    def _build_buf(self):
        pass

    def send_buf(self):
        if not len(self._buf):
            return 0
        return self._write(self._buf)

    def process_data(self, data, tm = None):
        self._retries = 0
        if not data:
            return 0
        if tm is None:
            tm = time()

        if self._bytes_to_read == -1:
            headers, data = str(data, 'utf-8').split("\r\n\r\n", 2)
            headers = headers.split("\r\n")
            self._data = data
            self._status = headers[0].split()
            cookies = None
            for h in headers:
                if h.startswith('Set-Cookie:'):
                    cookies = h[12:]
                    if self._bytes_to_read != -1:
                        break
                elif h.startswith('Content-Length:'):
                    self._bytes_to_read = int(h[15:]) - len(self._data)
                    if cookies:
                        break
            if cookies:
                self._cookies = cookies
        else:
            self._bytes_to_read -= len(data)
            self._data += str(data, 'utf-8')

        if self._bytes_to_read == -1:
            # No Content-Length header
            return self.stop()

        elif self._bytes_to_read == 0:
            follow = self._on_response(self._url, self._status[1], self._cookies, self._data, tm)
            self._state = self.READY
            return select.EPOLLOUT if follow else self.stop()

        self._state = self.WAIT_ANSWER
        return -1

    def _on_response(self, url, status, cookies, data, tm):
        pass

class OpenBmcHttpClient(HttpClient):
    def __init__(self, host, interval, user, passwd, points, keepalive=True, port=80):
        self._user = user
        self._passwd = passwd
        self._points = points
        super().__init__(host, interval, keepalive, port)
        self._recv = self._on_login
        self.post('/login', [self._user, self._passwd])

    def _on_response(self, url, status, cookies, data, tm):
        if status == '500':
            # Internal Server Error
            return False
        elif status == '401':
            # login failed
            if url == '/login':
                 return False
            self._cookies = None
            self._recv = self._on_login
            self.post('/login', [self._user, self._passwd])
            return True

        if cookies:
            self._cookies = cookies
        try:
            return self._recv(url, data, tm)
        except:
            return False

    def _on_login(self, url, data, tm):
        self._recv = self._on_rack_data
        self.get('/api/storage/rack', self._cookies)
        return True

    def _on_rack_data(self, url, data, tm):
        data = json_decode(data).get('data', '{"rack": {}}')
        data = json_decode(data['rack'])
        for k,p in self._points.items():
            self._process_points(p, data[k], data, tm)
        return False

    def _process_points(self, points, data, context, tm):
        if points is None:
            return
        if not type(data) == dict and not type(data) == list:
            self.on_data(points, context, data, tm)
            return
        if type(data) == dict:
            for k,p in data.items():
                self._process_points(points.get(k,None), p, data, tm)
        elif type(data) == list:
            for p in data:
                self._process_points(points, p, data, tm)

    def on_data(self, point, context, val, tm):
        print(point[0].format(**context), val, tm)

def main():
    cfg = {
        'host': 'vla-01-a-09a.rmm.ipmi.yandex.net',
        'interval': 3.0,
        'user': 'root',
        'passwd': '0penBmc',
        'points': {
            'zones': {
                'nodes': {
                    'power': ['node.{location}.power', 1], 'cpu_temp': ['node.{location}.cpu_temp', 1]
                },
            },
            'psus': {
                'temp_out': [ 'psu.{location}.temp_out', 1000.0 ], 'v_in': [ 'psu.{location}.v_in', 1 ]
            }
        }
    }
    from fsmsock import async
    c = OpenBmcHttpClient(**cfg)
    fsm = async.FSMSock()
    fsm.connect(c)
    while fsm.run():
        fsm.tick()

if __name__ == '__main__':
    main()
