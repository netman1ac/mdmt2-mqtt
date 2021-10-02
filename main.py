import hashlib
import json
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
        self._volumes = ['volume', 'music_volume']
        self._events = [
            'start_record', 'stop_record', 'start_talking', 'stop_talking', 'speech_recognized_success',
            'voice_activated',
            'music_status'] + self._volumes
        self._volumes_cmd_topics = {}
        self._volumes_stat_topics = {}

        self.BROKER_ADDRESS = self.cfg.gt('smarthome', 'ip')
        if not self.BROKER_ADDRESS:
            self.own.say('В настройках отсутствует ip адресс MQTT брокера')
            self.disable = True
            return
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
        self._sensors_order = ['binary_sensor', 'sensor', 'number']
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
            ]
        }
        self._prepare_discovery()
        self._mqtt = mqtt.Client(self.UNIQUE_ID, clean_session=False)

        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message = self._on_message
        self._mqtt.reconnect_delay_set(max_delay=600)

    def _on_connect(self, client, userdata, flags, rc):
        self.log('MQTT connected, subscribing to: {}'.format(self.TOPIC_CMD), logger.INFO)
        self._mqtt.subscribe(self.TOPIC_CMD, qos=1)
        for topic in self._volumes_cmd_topics:
            self._mqtt.subscribe(topic)
        self._send_discovery()
        self._send_availability(online=True)

    def _on_disconnect(self, client, userdata, rc):
        if not rc:
            return
        self.log('MQTT Disconnected, reconnecting. rc: {}'.format(rc), logger.CRIT)
        self._mqtt.reconnect()

    def _on_message(self, client, userdata, message: mqtt.MQTTMessage):
        try:
            if message.topic in self._volumes_cmd_topics:
                key = self._volumes_cmd_topics[message.topic]
                if key == 'music_volume':
                    key = 'mvolume'
                msg = {key: message.payload.decode("utf-8")}
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
        try:
            self._mqtt.connect(self.BROKER_ADDRESS)
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
        if name in self._volumes_stat_topics:
            if args:
                self._mqtt.publish(self._volumes_stat_topics[name], args[0])
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
                if sensor_type == 'number':
                    self._number_update(sensor)
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

    def _number_update(self, sensor: dict):
        cmd = sensor.pop('cmd')
        cmd_t = self.TOPIC + '/CTL/' + sensor['uniq_id']
        stat_t = self.TOPIC + '/STAT/' + sensor['uniq_id']
        self._volumes_cmd_topics[cmd_t] = cmd
        self._volumes_stat_topics[cmd] = stat_t
        sensor.update({'cmd_t': cmd_t, 'stat_t': stat_t})
        return sensor

    def _send_availability(self, online: bool):
        self._mqtt.publish(self._availability['topic'], 'online' if online else 'offline', retain=True)
