import json

import paho.mqtt.client as mqtt

import logger
from owner import Owner

NAME = 'mqtt'
API = 999
TERMINAL_VER_MIN = (0, 15, 10)

"""
MQTT топики для работы с терминалом
#topics:
TOPIC составляется из настройки terminal =  в секции smarthome и выставляется в "terminal" при отсутствии 
в настройках.
cmd: передача: текста терминалу для проговаривания голосом, команд для управления терминалом
conversation: прием терминалом текста для обработки в интеграции conversation
state: прием статусов терминала для автоматизаций 
Например в настройках установлено:
terminal = 'room' то топик будет room/cmd
terminal = то топик автоматически будет установлен в terminal/cmd


Команды принимаемые терминалом формате json
voice, tts', 'ask', 'volume', 'nvolume', 'listener'
Их описание: https://github.com/Aculeasis/mdmTerminal2/wiki/API-(draft)
поример
{"tts":"ТЕСТ"} - сказать "ТЕКСТ"
{"volume":"50"} - установить громкость терминала в 50%

 ###Home Assistant config###

Сенсоры для приема сообщений от терминала
#sensor
- platform: mqtt
  state_topic: "terminal/conversation"
  name: 'terminal_room'

- platform: mqtt
  state_topic: "terminal/state"
  name: 'terminal_room_state'

Загоняем пришедший текст в интеграцию conversation
#automation 
- alias: 'Terminal Room'
  trigger:
    platform: state
    entity_id: sensor.terminal_room
  action:
  - service: conversation.process
    data_template:
      text: '{{ states("sensor.terminal_room") }}'

Скрипт передачи текста терминалу для проговаривания голосом
#scripts
notify_mqtt:
  sequence:
  - service: mqtt.publish
    data_template:
      payload: '{"tts":"{{ message }}"}'
      topic: terminal/cmd

Пример использования в автоматизациях 
- alias: 'ТЕСТ'
  trigger:
    - platform: state
      entity_id:
        - binary_sensor.window_sensor
      to: 'on'
  action:
   - service: script.notify_mqtt
      data_template:
        message: "Тут текс для проговаривания терминалом"
 """


class Main:
    CMD = 'cmd'
    QRY = 'qry'

    def __init__(self, cfg, log, owner: Owner):
        self.cfg = cfg
        self.log = log
        self.own = owner
        self.disable = False
        self._events = (
            'start_record', 'stop_record', 'start_talking', 'stop_talking', 'speech_recognized_success',
            'voice_activated',
            'music_status')

        self.BROKER_ADDRESS = self.cfg.gt('smarthome', 'ip')
        if not self.BROKER_ADDRESS:
            self.own.say('В настройках отсутствует ip адресс MQTT брокера')
            self.disable = False
            return
        self.TOPIC = self.cfg.gt('smarthome', 'terminal') or 'terminal'
        self.TOPIC_CONVERSATION = self.TOPIC + '/conversation'
        self.TOPIC_CMD = self.TOPIC + '/cmd'
        self.TOPIC_STATE = self.TOPIC + '/state'

        self._mqtt = mqtt.Client(self.TOPIC)
        self._mqtt.on_message = self._on_message

    def _on_message(self, client, userdata, message):
        try:
            msg = json.loads(message.payload.decode("utf-8"))
        except Exception as e:
            self.log('_on_message error: {}'.format(e), logger.ERROR)
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
            self.disable = True
            return
        self.own.settings_from_srv({'smarthome': {'disable_http': True}})
        self._mqtt.subscribe(self.TOPIC_CMD)
        self._mqtt.loop_start()
        # Можно подписаться и на другие ивенты, потом не забыть отписаться.
        self.own.subscribe(self._events, self._callback)
        self.own.subscribe(self.CMD, self._publish_conversation)

    def join(self, *_, **__):
        if not self.disable:
            self.own.unsubscribe(self.CMD, self._publish_conversation)
            self.own.unsubscribe(self._events, self._callback)
            self._mqtt.loop_stop()

    def _callback(self, name, *args, **kwargs):
        self.log('send state: {} {} {}'.format(name, args, kwargs))
        self._mqtt.publish(self.TOPIC_STATE, name)

    def _publish_conversation(self, name, *args, **kwargs):
        self.log('send text: {} {} {}'.format(name, args, kwargs))
        msg = kwargs.get(self.QRY)
        if msg:
            self._mqtt.publish(self.TOPIC_CONVERSATION, msg)

    def _call_cmd(self, msg: dict):
        for key, value in msg.items():
            self.log('New command {}, data: {}'.format(key, repr(value)))
            if key in ['voice', 'tts', 'ask', 'volume', 'nvolume', 'listener']:
                self.own.terminal_call(key, value)
            else:
                self.own.say('Получена неизвестная команда')
