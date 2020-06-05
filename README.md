# mqtt plugin for mdmTerminal2
Позволяет работать с [терминал](https://github.com/Aculeasis/mdmTerminal2) по MQTT протоколу.

# Установка
```
mdmTerminal2/env/bin/python -m pip install paho-mqtt
cd mdmTerminal2/src/plugins
git clone https://github.com/netman1ac/mdmt2-mqtt.git
```
И перезапустить терминал.
```
systemctl restart mdmterminal2
```

## Настройка
Настройки хранятся в `mdmTerminal2/src/settings.ini`, в секции `"smarthome"`:
- **ip**: На который будут отправлены `mqtt` сообщения.
- **terminal**: топик. По умолчанию `terminal`.

# Дополнительно

```
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
'voice, 'tts', 'ask', 'volume', 'nvolume', 'listener'
Их описание: https://github.com/Aculeasis/mdmTerminal2/wiki/API-(draft)
Пример:
{"tts":"ТЕСТ"} - сказать "ТЕКСТ"
{"volume":"50"} - установить громкость терминала в 50%

 ###Home Assistant config###

Автоматизация для приема сообщений от терминала так как через сенсор работает с задержкой 
- alias: 'Room Voice Terminal'
  trigger:
    platform: mqtt
    topic: terminal/conversation
  action:
  - service: conversation.process
    data_template:
      text: "{{ trigger.payload }}"

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


Для управления терминалом из Lovelace Home Assistant 
Cоздаем скрипт:
voice_terminal_setting:
  sequence:
  - service: mqtt.publish
    data_template:
      payload: '{"{{ cmd }}":"{{ value }}"}'
      topic: terminal/cmd

Cоздаем input_boolean для включения/отключения микрофона терминала
voice_assist_listen:
  name: Терминал слушает
  initial: true

Создаем input_number для управления громкостью терминала
voice_assist_volume:
  name: Громкость терминала
  mode: slider
  initial: 100
  min: 1
  max: 100
  step: 1

И создаем две автоматизации:
- alias: 'Voice Terminal Switch Mic on off'
  trigger:
    platform: state
    entity_id: input_boolean.voice_assist_listen
  action: 
  - service: script.voice_terminal_setting
    data_template: 
      cmd: 'listener'
      value: '{{ states("input_boolean.voice_assist_listen") }}'
  - service: script.notify_mqtt
    data_template:
      message: >
        {% if is_state("input_boolean.voice_assist_listen", "on") %}
          'Включаю активацию по голосовой фразе'
        {% else %}
          'Выключаю активацию по голосовой фразе'
        {%endif%}


- alias: 'Set Voice Terminal Volume'
  trigger:
    platform: state
    entity_id: input_number.voice_assist_volume
  action: 
  - service: script.voice_terminal_setting
    data_template:
      cmd: 'volume'
      value: '{{ states("input_number.voice_assist_volume")|round }}'

```