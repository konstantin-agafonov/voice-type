# Voice Type: установка демона и триггера.
# Ставит Python-зависимости в виртуальное окружение ~/.venv/voice,
# копирует скрипты в ~/bin и файл автозапуска в ~/.config/autostart,
# подставляя реальный $HOME в строку Exec файла .desktop.

VENV          ?= $(HOME)/.venv/voice
BIN_DIR       ?= $(HOME)/bin
AUTOSTART_DIR ?= $(HOME)/.config/autostart
PY3           = python3

PROGRAM = voice-type
SCRIPT  = voice-type.py
DESKTOP = voice-daemon.desktop

.PHONY: install uninstall venv check

install: venv
	install -d $(BIN_DIR) $(AUTOSTART_DIR)
	install -m 0755 $(SCRIPT) $(PROGRAM) $(BIN_DIR)/
	sed "s|/home/nimda|$(HOME)|g" $(DESKTOP) > $(AUTOSTART_DIR)/$(DESKTOP)
	@echo "Установлено:"
	@echo "  $(BIN_DIR)/$(PROGRAM)"
	@echo "  $(BIN_DIR)/$(SCRIPT)"
	@echo "  $(AUTOSTART_DIR)/$(DESKTOP)"
	@echo "Демон запустится при следующем входе в систему;"
	@echo "ручной запуск: $(BIN_DIR)/$(PROGRAM) --daemon"

venv:
	@test -x $(VENV)/bin/python && test -x $(VENV)/bin/pip || { \
		rm -rf $(VENV); \
		python3 -m venv $(VENV) \
			|| { echo "Не удалось создать окружение. Установите python3-venv:"; \
			     echo "  sudo apt install python$$($(PY3) -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')-venv"; \
			     exit 1; }; \
	}
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r requirements.txt
	@echo "Окружение готово: $(VENV)"

uninstall:
	rm -f $(BIN_DIR)/$(PROGRAM) $(BIN_DIR)/$(SCRIPT) $(AUTOSTART_DIR)/$(DESKTOP)
	@echo "Удалено."

check:
	@for tool in xdotool xclip; do \
		command -v $$tool >/dev/null 2>&1 \
			&& echo "OK: $$tool" \
			|| echo "ОТСУТСТВУЕТ: $$tool (sudo apt install $$tool)"; \
	done
