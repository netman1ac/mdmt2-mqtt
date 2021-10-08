import hashlib
import json
import urllib.parse
import uuid

import paho.mqtt.client as mqtt

import logger
from owner import Owner

NAME = 'mqtt'
API = 999
TERMINAL_VER_MIN = (0, 18, 7)


def unique_id():
    return 'mdmt2_' + hashlib.md5(bytes(str(uuid.getnode()), "utf-8")).hexdigest()[:6]


def dumps(data: list or dict) -> str:
    return json.dumps(data, ensure_ascii=False)


class Main:
    CMD = 'cmd'
    QRY = 'qry'

    def __init__(self, cfg, log, owner: Owner):
        self.cfg = cfg
        self.log = log
        self.own = owner
        self.disable = False
        self._controllable = ['volume', 'music_volume', 'listener']
        self._events = [
            'start_record', 'stop_record', 'start_talking', 'stop_talking', 'speech_recognized_success',
            'voice_activated',
            'music_status'] + self._controllable
        self._controllable_ctl_topics = {}
        self._controllable_stat_topics = {}
        # https://www.home-assistant.io/docs/mqtt/birth_will/
        self._ha_status_topic = 'homeassistant/status'

        if not self.cfg.gt('smarthome', 'ip'):
            self.own.say('В настройках отсутствует ip адресс MQTT брокера')
            self.disable = True
            return
        self._mqtt_data = self._mqtt_data_filling()
        self.UNIQUE_ID = self.cfg.gt('smarthome', 'terminal') or unique_id()
        self.TOPIC = 'terminals/' + self.UNIQUE_ID
        self.TOPIC_CONVERSATION = self.TOPIC + '/conversation'
        self.TOPIC_CMD = self.TOPIC + '/cmd'
        self.TOPIC_STATE = self.TOPIC + '/state'

        self._device = {
            'ids': self.UNIQUE_ID,
            'mf': 'Aculeasis',
            'mdl': 'Smart Speaker',
            'name': 'mdmTerminal2',
            'sw': self.cfg.version_str
        }
        self._availability = {
            'topic': self.TOPIC + '/availability',
        }
        # Для генерации uniq_id
        self._sensors_order = ['binary_sensor', 'sensor', 'number', 'switch']
        self._sensors = {
            'binary_sensor': [
                {'name': 'record',
                 'icon': 'hass:microphone',
                 'stat_t': self.TOPIC_STATE,
                 'pl_on': 'start_record',
                 'pl_off': 'stop_record',
                 'val_tpl': '{{ value_json.state }}',
                 },
                {'name': 'talking',
                 'dev_cla': 'sound',
                 'stat_t': self.TOPIC_STATE,
                 'pl_on': 'start_talking',
                 'pl_off': 'stop_talking',
                 'val_tpl': '{{ value_json.state }}',
                 },
            ],
            'sensor': [
                {'name': 'say',
                 'icon': 'hass:face-recognition',
                 'frc_upd' : True,
                 'stat_t': self.TOPIC_CONVERSATION,
                 },
            ],
            'number': [
                {'cmd': 'volume',
                 'name': 'Volume',
                 'icon': 'hass:volume-vibrate',
                 },
                {'cmd': 'music_volume',
                 'name': 'Music Volume',
                 'icon': 'hass:volume-vibrate',
                 },
            ],
            'switch': [
                {'cmd': 'listener',
                 'name': 'Mic enabled',
                 'icon': 'hass:microphone-settings',
                 'pl_on': 'on',
                 'pl_off': 'off'
                 },
            ],
        }
        self._prepare_discovery()
        self._mqtt = mqtt.Client(self.UNIQUE_ID, clean_session=False, transport=self._mqtt_data['type'])
        if self._mqtt_data['type'] == 'websockets':
            self._mqtt.ws_set_options(path=self._mqtt_data['path'])
        if self._mqtt_data['username']:
            self._mqtt.username_pw_set(self._mqtt_data['username'], self._mqtt_data['password'])
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message = self._on_message
        self._mqtt.reconnect_delay_set(max_delay=600)

    def _mqtt_data_filling(self) -> dict:
        values = {
            'type': 'tcp',
            'addr': self.cfg.gt('smarthome', 'ip'),
            'port': 1883, 'username': None,
            'password': None,
            'path': '/'
        }
        if self.cfg.gt('smarthome', 'username'):
            values.update(
                {'username': self.cfg.gt('smarthome', 'username'), 'password': self.cfg.gt('smarthome', 'password')}
            )
        pr = urllib.parse.urlparse(self.cfg.gt('smarthome', 'ip'))
        try:
            ip_data = {
                'type': pr.scheme or values['type'],
                'addr': pr.hostname or values['addr'],
                'port': pr.port or values['port'],
                'path': pr.path or values['path'],
            }
            if pr.username and pr.password:
                ip_data.update({'username': pr.username, 'password': pr.password})
        except ValueError as e:
            self.log('Error Parsing [smarthome] ip, it will be used as is: {}'.format(e), logger.ERROR)
        else:
            values.update(ip_data)
        # TODO: SSL, TLS и т.д.
        values['type'] = 'websockets' if values['type'] in ('ws', 'wss') else 'tcp'
        return values

    def _on_connect(self, client, userdata, flags, rc):
        self.log('MQTT connected, subscribing to: {}'.format(self.TOPIC_CMD), logger.INFO)
        self._mqtt.subscribe(self.TOPIC_CMD, qos=1)
        for topic in self._controllable_ctl_topics:
            self._mqtt.subscribe(topic)
        self._mqtt.subscribe(self._ha_status_topic)
        self._send_initial_data()

    def _send_initial_data(self):
        self._send_discovery()
        self._send_availability(online=True)
        self._send_default_values()

    def _on_disconnect(self, client, userdata, rc):
        if not rc:
            return
        self.log('MQTT Disconnected, reconnecting. rc: {}'.format(rc), logger.CRIT)
        self._mqtt.reconnect()

    def _on_message(self, client, userdata, message: mqtt.MQTTMessage):
        try:
            if message.topic in self._controllable_ctl_topics:
                key = self._controllable_ctl_topics[message.topic]
                if key == 'music_volume':
                    key = 'mvolume'
                msg = {key: message.payload.decode("utf-8")}
            elif message.topic == self._ha_status_topic:
                msg = None
                status = message.payload.decode("utf-8")
                self.log('Home Assistant: {}'.format(status))
                if status == 'online':
                    self._send_initial_data()
            else:
                msg = json.loads(message.payload.decode("utf-8"), strict=False)
        except Exception as e:
            self.log('_on_message error: {}'.format(e), logger.ERROR)
            if type(e) in (json.decoder.JSONDecodeError, TypeError):
                self.log('Message: {}'.format(message.payload.decode("utf-8")), logger.ERROR)
                self.own.say('Сообщение не в JSON формате')
        else:
            if msg:
                self._call_cmd(msg)

    def start(self):
        add_msg = []
        if self._mqtt_data['path'] and self._mqtt_data['type'] == 'websockets':
            add_msg.append('path: {}'.format(self._mqtt_data['path']))
        if self._mqtt_data['username']:
            add_msg.append('username: {}'.format(self._mqtt_data['username']))
            if self._mqtt_data['password']:
                add_msg.append('password present')
        if add_msg:
            add_msg.insert(0, '')
        self.log('Connecting to {}:{} through {}{}'.format(
            self._mqtt_data['addr'], self._mqtt_data['port'], self._mqtt_data['type'], ', '.join(add_msg))
        )
        try:
            self._mqtt.connect(host=self._mqtt_data['addr'], port=self._mqtt_data['port'])
        except (OSError, ConnectionRefusedError) as e:
            self.own.say('Ошибка подключения к MQTT брокеру')
            self.log('MQTT connecting error: {}'.format(e), logger.CRIT)
            return
        self.own.settings_from_srv({'smarthome': {'disable_http': True}})
        self._mqtt.loop_start()
        # Можно подписаться и на другие ивенты, потом не забыть отписаться.
        self.own.subscribe(self._events, self._callback)
        self.own.subscribe(self.CMD, self._publish_conversation)

    def join(self, *_, **__):
        if not self.disable:
            self.own.unsubscribe(self.CMD, self._publish_conversation)
            self.own.unsubscribe(self._events, self._callback)
            self._mqtt.loop_stop()
            self._send_availability(online=False)
            self._mqtt.disconnect()

    def _callback(self, name, *args, **kwargs):
        self.log('send state: {} {} {}'.format(name, args, kwargs))
        if name in self._controllable_stat_topics:
            if args and not isinstance(args[0], int) or 0 <= args[0] <= 100:
                self._mqtt.publish(self._controllable_stat_topics[name], args[0])
        else:
            self._mqtt.publish(self.TOPIC_STATE, dumps({'state': name, 'args': args, 'kwargs': kwargs}))

    def _publish_conversation(self, name, *args, **kwargs):
        self.log('send text: {} {} {}'.format(name, args, kwargs))
        msg = kwargs.get(self.QRY)
        if msg:
            self._mqtt.publish(self.TOPIC_CONVERSATION, msg)

    def _call_cmd(self, msg: dict):
        for key, value in msg.items():
            self.log('New command {}, data: {}'.format(key, repr(value)))
            if key in ['voice', 'tts', 'ask', 'volume', 'mvolume', 'listener']:
                self.own.terminal_call(key, value)
            else:
                self.own.say('Получена неизвестная команда')

    def _prepare_discovery(self):
        idx = 1
        for sensor_type in self._sensors_order:
            for sensor in self._sensors[sensor_type]:
                self._all_update(sensor, idx)
                if sensor_type in ['number', 'switch']:
                    self._controllable_update(sensor)
                idx += 1

    def _send_discovery(self):
        for sensor_type, sensors in self._sensors.items():
            for sensor in sensors:
                self._mqtt.publish('homeassistant/{}/{}/config'.format(sensor_type, sensor['uniq_id']), dumps(sensor))

    def _all_update(self, sensor: dict, idx: int):
        sensor.update(
            {'uniq_id': '{}{}'.format(self.UNIQUE_ID, idx), 'dev': self._device, 'avty': [self._availability]}
        )
        return sensor

    def _controllable_update(self, sensor: dict):
        cmd = sensor.pop('cmd')
        cmd_t = self.TOPIC + '/CTL/' + sensor['uniq_id']
        stat_t = self.TOPIC + '/STAT/' + sensor['uniq_id']
        self._controllable_ctl_topics[cmd_t] = cmd
        self._controllable_stat_topics[cmd] = stat_t
        sensor.update({'cmd_t': cmd_t, 'stat_t': stat_t})
        return sensor

    def _send_availability(self, online: bool):
        self._mqtt.publish(self._availability['topic'], 'online' if online else 'offline', retain=True)

    def _send_default_values(self):
        volume = self.own.get_volume_status
        self._callback('volume', volume['volume'])
        self._callback('music_volume', volume['music_volume'])
        self._callback('listener', 'on' if self.own.terminal_listen() else 'off')
