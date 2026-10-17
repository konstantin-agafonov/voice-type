# Voice Type: установка демона и триггера.
# Копирует скрипты в ~/bin и файл автозапуска в ~/.config/autostart,
# подставляя реальный $HOME в строку Exec файла .desktop.

BIN_DIR       ?= $(HOME)/bin
AUTOSTART_DIR ?= $(HOME)/.config/autostart

PROGRAM = voice-type
SCRIPT  = voice-type.py
DESKTOP = voice-daemon.desktop

.PHONY: install uninstall

install:
	install -d $(BIN_DIR) $(AUTOSTART_DIR)
	install -m 0755 $(SCRIPT) $(PROGRAM) $(BIN_DIR)/
	sed "s|/home/nimda|$(HOME)|g" $(DESKTOP) > $(AUTOSTART_DIR)/$(DESKTOP)
	@echo "Установлено:"
	@echo "  $(BIN_DIR)/$(PROGRAM)"
	@echo "  $(BIN_DIR)/$(SCRIPT)"
	@echo "  $(AUTOSTART_DIR)/$(DESKTOP)"
	@echo "Демон запустится при следующем входе в систему;"
	@echo "ручной запуск: $(BIN_DIR)/$(PROGRAM) --daemon"

uninstall:
	rm -f $(BIN_DIR)/$(PROGRAM) $(BIN_DIR)/$(SCRIPT) $(AUTOSTART_DIR)/$(DESKTOP)
	@echo "Удалено."
