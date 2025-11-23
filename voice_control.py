import speech_recognition as sr
import pyautogui
import pyperclip
import threading
import time
import queue
import tkinter as tk
from tkinter import ttk
import json
import os
import datetime
import tempfile
from gtts import gTTS
import pygame
import re

class OverlayWindow:
    def __init__(self):
        self.config_file = "overlay_config.json"
        
        # Создаем окно
        self.root = tk.Tk()
        self.setup_window()
        
        # Переменные для перемещения
        self.x_offset = 0
        self.y_offset = 0
        self.dragging = False
        
        # Данные для отображения
        self.session_time = "00:00:00"
        self.battle_count = 0
        self.avg_damage = 0
        self.win_rate = 0
        self.tank_name = "-"
        self.session_start_time = None
        
        # Загружаем сохраненное положение
        self.load_window_position()
        
    def setup_window(self):
        self.root.title("Танки Статистика")
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.8)
        self.root.configure(bg='#1a1a1a')
        
        # ФИКСИРОВАННЫЙ РАЗМЕР ОКНА
        window_width = 350
        window_height = 110
        
        # Начальное положение (будет перезаписано из конфига)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = screen_width - window_width - 50
        y = screen_height - window_height - 50
        self.root.geometry(f'{window_width}x{window_height}+{x}+{y}')
        self.root.resizable(False, False)
        
        # Создаем основной контейнер
        self.container = tk.Frame(self.root, bg='#1a1a1a')
        self.container.pack(expand=True, fill='both', padx=10, pady=5)
        
        # Привязываем события для перемещения
        self.container.bind("<ButtonPress-1>", self.start_move)
        self.container.bind("<ButtonRelease-1>", self.stop_move)
        self.container.bind("<B1-Motion>", self.do_move)
        
        # Верхняя строка - название танка и время сессии
        self.top_frame = tk.Frame(self.container, bg='#1a1a1a')
        self.top_frame.pack(fill=tk.X, pady=(0, 5))
        
        font_size = 14
        
        # Название танка
        self.tank_label = tk.Label(
            self.top_frame,
            text='Танк: -',
            font=('Segoe UI', font_size, 'bold'),
            fg='#ffffff',
            bg='#1a1a1a'
        )
        self.tank_label.pack(side=tk.LEFT)
        
        # Время сессии
        self.session_label = tk.Label(
            self.top_frame,
            text='Время: 00:00:00',
            font=('Segoe UI', font_size, 'bold'),
            fg='#ffff00',
            bg='#1a1a1a'
        )
        self.session_label.pack(side=tk.RIGHT)
        
        # Средняя строка - бои и Win Rate
        self.middle_frame = tk.Frame(self.container, bg='#1a1a1a')
        self.middle_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.battles_label = tk.Label(
            self.middle_frame,
            text='Бои: 0',
            font=('Segoe UI', font_size, 'bold'),
            fg='#00ff00',
            bg='#1a1a1a'
        )
        self.battles_label.pack(side=tk.LEFT)
        
        self.winrate_label = tk.Label(
            self.middle_frame,
            text='Win Rate: 0%',
            font=('Segoe UI', font_size, 'bold'),
            fg='#00ff00',
            bg='#1a1a1a'
        )
        self.winrate_label.pack(side=tk.RIGHT)
        
        # Нижняя строка - средний урон и текущий урон в бою
        self.bottom_frame = tk.Frame(self.container, bg='#1a1a1a')
        self.bottom_frame.pack(fill=tk.X)
        
        # Средний урон слева
        self.damage_label = tk.Label(
            self.bottom_frame,
            text='Ср. урон: 0',
            font=('Segoe UI', font_size, 'bold'),
            fg='#00ff00',
            bg='#1a1a1a'
        )
        self.damage_label.pack(side=tk.LEFT)
        
        # Текущий урон в бою справа
        self.current_damage_label = tk.Label(
            self.bottom_frame,
            text='Урон: 0',
            font=('Segoe UI', font_size, 'bold'),
            fg='#ff9900',  # Оранжевый цвет для текущего урона
            bg='#1a1a1a'
        )
        self.current_damage_label.pack(side=tk.RIGHT)
        
    def start_move(self, event):
        self.x_offset = event.x
        self.y_offset = event.y
        self.dragging = True
        
    def stop_move(self, event):
        self.dragging = False
        self.save_window_position()
        
    def do_move(self, event):
        if self.dragging:
            x = self.root.winfo_pointerx() - self.x_offset
            y = self.root.winfo_pointery() - self.y_offset
            self.root.geometry(f"+{x}+{y}")
        
    def save_window_position(self):
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        config = {
            'x': x,
            'y': y
        }
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f)
        except:
            pass
            
    def load_window_position(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                x = config.get('x', 100)
                y = config.get('y', 100)
                self.root.geometry(f"+{x}+{y}")
        except:
            pass
            
    def update_stats(self, tank_name, session_start_time, battle_count, avg_damage, win_rate, current_damage=0, last_damage=0):
        self.tank_name = tank_name
        self.session_start_time = session_start_time
        self.battle_count = battle_count
        self.avg_damage = avg_damage
        self.win_rate = win_rate
        self.current_damage = current_damage
        self.last_damage = last_damage
        
    def update_display(self):
        # Обновляем время сессии
        if self.session_start_time:
            session_duration = datetime.datetime.now() - self.session_start_time
            hours, remainder = divmod(int(session_duration.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            session_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            session_time = "00:00:00"
        
        # Обновляем метки
        self.tank_label.config(text=f"Танк: {self.tank_name}")
        self.session_label.config(text=f"Время: {session_time}")
        self.battles_label.config(text=f"Бои: {self.battle_count}")
        self.winrate_label.config(text=f"Win Rate: {self.win_rate}%")
        self.damage_label.config(text=f'Ср. урон: {self.avg_damage}')
        self.current_damage_label.config(text=f'Урон: {self.current_damage} + {self.last_damage}')
        
        # Планируем следующее обновление
        self.root.after(1000, self.update_display)
        
    def run(self):
        # Запускаем обновление дисплея
        self.update_display()
        try:
            self.root.mainloop()
        except:
            pass

class VoiceControlGUI:
    def __init__(self, tank_stats_recorder):
        self.tank_stats_recorder = tank_stats_recorder
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.is_listening = False
        self.command_queue = queue.Queue()
        self.recognition_thread = None
        self.processing_thread = None
        
        # Настройки
        self.recognition_timeout = 5
        self.phrase_time_limit = 10
        
        # Создаем GUI
        self.setup_gui()
        
        # Калибруем микрофон
        self.calibrate_microphone()
    
    def setup_gui(self):
        """Настройка графического интерфейса"""
        self.root = tk.Toplevel()
        self.root.title("Голосовой контроль для танков")
        self.root.geometry("400x200")
        self.root.resizable(False, False)
        
        # Стиль
        style = ttk.Style()
        style.configure("TButton", padding=6, relief="flat", background="#ccc")
        
        # Основная рамка
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Статус
        self.status_var = tk.StringVar(value="Готов к работе")
        status_label = ttk.Label(main_frame, textvariable=self.status_var, 
                                font=("Arial", 12, "bold"))
        status_label.pack(pady=10)
        
        # Кнопка запуска/остановки
        self.toggle_button = ttk.Button(main_frame, text="Запустить прослушивание", 
                                       command=self.toggle_listening)
        self.toggle_button.pack(pady=5)
        
        # Последнее распознанное сообщение
        ttk.Label(main_frame, text="Последнее сообщение:", font=("Arial", 10)).pack(pady=(20, 5))
        
        self.message_var = tk.StringVar(value="—")
        message_label = ttk.Label(main_frame, textvariable=self.message_var, 
                                 font=("Arial", 10), wraplength=380, justify=tk.CENTER)
        message_label.pack(pady=5)
        
        # Инструкция
        instruction = ttk.Label(main_frame, 
                       text="Распознанная речь обрабатывается как команды или отправляется в чат",
                       font=("Arial", 9), foreground="gray")
        instruction.pack(pady=10)
    
    def calibrate_microphone(self):
        """Калибровка микрофона"""
        self.status_var.set("Калибровка микрофона...")
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=2)
        self.status_var.set("Калибровка завершена")
    
    def toggle_listening(self):
        """Запуск/остановка прослушивания"""
        if not self.is_listening:
            self.start_listening()
        else:
            self.stop_listening()
    
    def start_listening(self):
        """Запуск прослушивания"""
        self.is_listening = True
        self.toggle_button.config(text="Остановить прослушивание")
        self.status_var.set("Прослушивание активно...")
        
        # Запускаем поток распознавания
        self.recognition_thread = threading.Thread(target=self.recognition_worker, daemon=True)
        self.recognition_thread.start()
        
        # Запускаем поток обработки команд
        self.processing_thread = threading.Thread(target=self.processing_worker, daemon=True)
        self.processing_thread.start()
    
    def stop_listening(self):
        """Остановка прослушивания"""
        self.is_listening = False
        self.toggle_button.config(text="Запустить прослушивание")
        self.status_var.set("Прослушивание остановлено")
    
    def recognition_worker(self):
        """Поток для непрерывного распознавания речи"""
        while self.is_listening:
            try:
                # Слушаем с коротким таймаутом для возможности прерывания
                with self.microphone as source:
                    audio = self.recognizer.listen(
                        source, 
                        timeout=1.0,
                        phrase_time_limit=8
                    )
                
                # Распознаем речь
                text = self.recognizer.recognize_google(audio, language="ru-RU").lower()
                
                if text:
                    self.command_queue.put(text)
                    print(f"Распознано: {text}")
                    
            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except Exception as e:
                if self.is_listening:
                    print(f"Ошибка распознавания: {e}")
                continue
    
    def processing_worker(self):
        """Поток для обработки распознанных команд"""
        while self.is_listening:
            try:
                command = self.command_queue.get(timeout=0.5)
                self.process_command(command)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Ошибка обработки команды: {e}")
    
    def process_command(self, command):
        """Обработка распознанной команды"""
        if command and len(command.strip()) > 0:
            # Проверяем, является ли команда сообщением для чата
            if command.startswith("чат "):
                message = command[4:].strip()  # Убираем "чат " из начала
                if message:
                    self.status_var.set("Отправляю сообщение в чат...")
                    self.root.update()
                    
                    self.send_to_chat(message)
                    self.message_var.set(f'Чат: "{message}"')
                    self.status_var.set("Сообщение отправлено!")
                    return
            
            # Если не команда чата, передаем в основной обработчик команд
            self.tank_stats_recorder.command_queue.put(command)
            self.message_var.set(f'Команда: "{command}"')
            self.status_var.set("Команда обработана")
    
    def send_to_chat(self, message):
        """Отправка сообщения в игровой чат"""
        try:
            # Копируем сообщение в буфер обмена
            pyperclip.copy(message)
            
            # Небольшая пауза перед действиями
            time.sleep(0.1)
            
            # Нажимаем Enter для открытия чата
            pyautogui.press('enter')
            time.sleep(0.1)
            
            # Вставляем текст из буфера обмена
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            
            # Нажимаем Enter для отправки сообщения
            pyautogui.press('enter')
            time.sleep(0.1)
            
            print(f"Сообщение отправлено в чат: {message}")
            
        except Exception as e:
            print(f"Ошибка отправки сообщения: {e}")
            self.status_var.set("Ошибка отправки")
    
    def run(self):
        """Запуск GUI"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.stop_listening()
        finally:
            self.is_listening = False

class TankStatsRecorder:
    def __init__(self, overlay):
        self.data_file = "tank_stats.json"
        self.current_session = None
        self.current_battle_damage = 0
        self.recognizer = sr.Recognizer()
        self.is_speaking = False
        self.command_queue = queue.Queue()
        self.listening_active = True
        self.damage_history = []  # История урона для отмены
        self.last_damage = 0
        
        # Ссылка на оверлей
        self.overlay = overlay
        
        # Настройки для уменьшения ложных срабатываний
        self.recognizer.energy_threshold = 3000
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.pause_threshold = 1.0
        self.recognizer.phrase_threshold = 0.3  # Порог для фразы
        
        # Инициализация pygame для воспроизведения звука
        pygame.mixer.init()
        
        self.load_data()
        self.update_overlay()
        
    def load_data(self):
        """Загрузка данных из файла"""
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        else:
            self.data = {"sessions": []}
    
    def save_data(self):
        """Сохранение данных в файл"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
    
    def update_overlay(self):
        """Обновление данных в оверлее"""
        if self.current_session:
            tank_name = self.current_session['tank_name']
            session_start_time = datetime.datetime.fromisoformat(self.current_session['start_time'])
            battle_count = self.current_session['battles_count']
            victories = self.current_session['victories_count']
            total_damage = self.current_session['total_damage']
            last_damage = self.current_session.get('last_damage', 0)
            
            # Расчет статистики
            avg_damage = total_damage // battle_count if battle_count > 0 else 0
            win_rate = round((victories / battle_count) * 100) if battle_count > 0 else 0
            
            # Передаем текущий урон в оверлей
            self.overlay.update_stats(tank_name, session_start_time, battle_count, avg_damage, win_rate, self.current_battle_damage, last_damage)
        else:
            # Если сессии нет, показываем прочерки и урон 0
            self.overlay.update_stats("-", None, 0, 0, 0, 0, 0)
    
    def speak(self, text):
        """Озвучивание текста через gTTS"""
        print(f"Озвучка: {text}")
        self.is_speaking = True
        
        def speak_thread():
            try:
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                    tmp_filename = tmp_file.name
                
                tts = gTTS(text=text, lang='ru')
                tts.save(tmp_filename)
                time.sleep(0.5)
                
                pygame.mixer.music.load(tmp_filename)
                pygame.mixer.music.play()
                
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
                
                try:
                    os.unlink(tmp_filename)
                except:
                    pass
                    
            except Exception as e:
                print(f"Ошибка озвучивания: {e}")
            finally:
                self.is_speaking = False
        
        thread = threading.Thread(target=speak_thread)
        thread.daemon = True
        thread.start()
    
    def listen_continuous(self):
        """Непрерывное прослушивание в отдельном потоке"""
        def listening_thread():
            with sr.Microphone() as source:
                # Длительная калибровка фонового шума
                print("Калибровка микрофона...")
                self.recognizer.adjust_for_ambient_noise(source, duration=5)  # Увеличили время калибровки
                print("Калибровка завершена. Начинаю слушать...")
                
                while self.listening_active:
                    try:
                        # Не слушаем во время озвучки
                        if self.is_speaking:
                            time.sleep(0.1)
                            continue
                            
                        #print("Слушаю...")
                        audio = self.recognizer.listen(
                            source, 
                            timeout=1.0,  # Увеличили таймаут
                            phrase_time_limit=5  # Увеличили лимит времени фразы
                        )
                        
                        # Распознавание в отдельном потоке
                        def recognition_thread(audio_data):
                            try:
                                # Пробуем разные сервисы распознавания
                                text = self.recognize_speech(audio_data)
                                if text and text.strip():
                                    print(f"Распознано: {text}")
                                    self.command_queue.put(text.lower())
                            except Exception as e:
                                print(f"Ошибка распознавания: {e}")
                        
                        recog_thread = threading.Thread(target=recognition_thread, args=(audio,))
                        recog_thread.daemon = True
                        recog_thread.start()
                        
                    except sr.WaitTimeoutError:
                        continue
                    except Exception as e:
                        if self.listening_active:
                            print(f"Ошибка прослушивания: {e}")
                        continue
        
        thread = threading.Thread(target=listening_thread)
        thread.daemon = True
        thread.start()

    def recognize_speech(self, audio_data):
        """Распознавание речи с использованием нескольких методов"""
        try:
            # Пробуем Google распознавание с явными параметрами
            return self.recognizer.recognize_google(
                audio_data, 
                language="ru-RU",
                show_all=False
            )
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            print(f"Ошибка Google распознавания: {e}")
            return ""

    def extract_number(self, text):
        """УЛУЧШЕННОЕ извлечение числа из текста"""
        try:
            # Убираем все не цифры, кроме пробелов (для разделения чисел)
            clean_text = re.sub(r'[^\d\s]', '', text)
            
            # Ищем все последовательности цифр
            numbers = re.findall(r'\d+', clean_text)
            
            if numbers:
                # Берем последнее найденное число (часто это самое важное)
                return int(numbers[-1])
            
            # Дополнительная проверка для словесного обозначения чисел
            number_words = {
                'сто': 100, 'двести': 200, 'триста': 300, 'четыреста': 400,
                'пятьсот': 500, 'шестьсот': 600, 'семьсот': 700, 
                'восемьсот': 800, 'девятьсот': 900,
                'тысяча': 1000, 'две тысячи': 2000, 'три тысячи': 3000
            }
            
            for word, num in number_words.items():
                if word in text.lower():
                    return num
            
            return None
        except Exception as e:
            print(f"Ошибка извлечения числа: {e}")
            return None
    
    def extract_tank_name(self, command):
        """Извлечение названия танка из команды 'новая сессия'"""
        prefixes = ["новая", "нова"]
        
        for prefix in prefixes:
            if prefix in command:
                tank_name = command.split(prefix, 1)[1].strip()
                return tank_name if tank_name else None
        
        return None
    
    def start_new_session(self, tank_name):
        """Начало новой сессии"""
        session = {
            "tank_name": tank_name,
            "start_time": datetime.datetime.now().isoformat(),
            "end_time": None,
            "battles": [],
            "total_damage": 0,
            "last_damage" : 0,
            "battles_count": 0,
            "victories_count": 0
        }
        
        self.data["sessions"].append(session)
        self.current_session = self.data["sessions"][-1]
        self.current_battle_damage = 0
        self.last_damage = 0
        self.save_data()
        
        self.speak(f"{tank_name}")
        self.update_overlay()
    
    def add_damage(self, damage):
        """Добавление урона к текущему бою"""
        if not self.current_session:
            self.speak("Сначала начните новую сессию")
            return
        
        self.current_battle_damage += damage
        self.damage_history.append(damage)  # Сохраняем в историю для отмены
        self.last_damage = damage
        
        # Сохраняем последний урон в сессии
        self.current_session["last_damage"] = damage
        
        print(f"Добавлен урон: {damage}. Текущий урон в бою: {self.current_battle_damage}")
        self.update_overlay()

    def end_battle(self, result):
        """Завершение боя с указанием результата"""
        if not self.current_session:
            self.speak("Нет активной сессии")
            return
        
        battle = {
            "damage": self.current_battle_damage,
            "result": result,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        self.current_session["battles"].append(battle)
        self.current_session["total_damage"] += self.current_battle_damage
        self.current_session["battles_count"] += 1
        
        if result == "victory":
            self.current_session["victories_count"] += 1
        
        # Сбрасываем урон текущего боя
        self.current_battle_damage = 0
        self.damage_history.clear()
        self.save_data()
        
        self.speak_session_stats()
        self.update_overlay()
    
    def speak_session_stats(self):
        """Озвучивание статистики сессии"""
        battles_count = self.current_session["battles_count"]
        victories_count = self.current_session["victories_count"]
        total_damage = self.current_session["total_damage"]
        
        if battles_count > 0:
            win_rate = (victories_count / battles_count) * 100
            avg_damage = total_damage / battles_count
            self.speak(f"{win_rate:.0f} процентов. {avg_damage:.0f}")
        else:
            self.speak("В сессии пока не было боёв")
    
    def end_session(self):
        """Завершение текущей сессии"""
        if self.current_session:
            self.current_session["end_time"] = datetime.datetime.now().isoformat()
            
            battles_count = self.current_session["battles_count"]
            victories_count = self.current_session["victories_count"]
            total_damage = self.current_session["total_damage"]
            
            if battles_count > 0:
                win_rate = (victories_count / battles_count) * 100
                avg_damage = total_damage / battles_count
                self.speak(f"Сессия завершена. {win_rate:.0f} процентов, {avg_damage:.0f}")
            else:
                self.speak("Сессия завершена. Боев не было")
            
            self.current_session = None
            self.current_battle_damage = 0
            self.damage_history.clear()
            self.save_data()
            self.update_overlay()
        else:
            self.speak("Нет активной сессии")
    
    def cancel_last_damage(self):
        """Отмена последнего добавленного урона"""
        if not self.damage_history:
            self.speak("Нечего отменять")
            return
        
        last_damage = self.damage_history.pop()
        self.current_battle_damage -= last_damage
        
        self.speak(f"Отменён урон {last_damage}")
        print(f"Отменён урон: {last_damage}. Текущий урон: {self.current_battle_damage}")
        self.update_overlay()

    def process_command(self, command):
        """Обработка голосовой команды с правильным приоритетом"""
        if not command:
            return
        
        print(f"Обрабатываю команду: {command}")
        
        # Команда помощи
        if any(word in command for word in ["помощь", "справка", "команды"]):
            self.show_help()
            return
        
        # Команда отмены
        if any(word in command for word in ["отмена", "отмени", "верни", "назад"]):
            self.cancel_last_damage()
            return
        
        # В первую очередь проверяем команду "Новая сессия"
        tank_name = self.extract_tank_name(command)
        if tank_name:
            self.start_new_session(tank_name)
            return
        
        # Затем проверяем другие команды
        if any(word in command for word in ["победа", "виктори", "выиграл", "победил"]):
            self.end_battle("victory")
            return
        
        elif any(word in command for word in ["поражение", "поражен", "проиграл", "луз"]):
            self.end_battle("defeat")
            return
        
        elif any(word in command for word in ["конец сессии", "завершить сессию", "стоп сессия"]):
            self.end_session()
            return
        
        elif any(word in command for word in ["статус", "статистика", "показать"]):
            if self.current_session:
                self.speak_session_stats()
            else:
                self.speak("Нет активной сессии")
            return
        
        # И только в последнюю очередь проверяем числа (урон)
        damage = self.extract_number(command)
        if damage is not None:
            self.add_damage(damage)
            return
        
        # Игнорируем нераспознанные команды
        print(f"Игнорирую нераспознанную команду: {command}")

    def show_help(self):
        """Показать справку по командам"""
        help_text = """
ДОСТУПНЫЕ КОМАНДЫ:
- "Новая сессия [название танка]" - начать новую сессию (можно несколько слов)
- "[число]" - добавить урон (например: "100", "250")
- "Победа" - завершить бой победой
- "Поражение" - завершить бой поражением
- "Конец сессии" - завершить текущую сессию
- "Статус" - показать текущую статистику
- "Чат [сообщение]" - отправить сообщение в игровой чат
- "Отмена" - отменить последний добавленный урон
- "Помощь" - показать эту справку
"""
        print(help_text)
        self.speak("Справка показана в консоли")
    
    def run(self):
        """Основной цикл программы"""
        self.speak("Программа запущена")
        print("\nПрограмма готова к работе. Скажите 'Помощь' для просмотра команд.")
        
        # Запускаем непрерывное прослушивание в фоне
        self.listen_continuous()
        
        try:
            while True:
                # Обрабатываем команды из очереди
                try:
                    command = self.command_queue.get(timeout=0.1)
                    self.process_command(command)
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"Ошибка обработки команды: {e}")
                    
        except KeyboardInterrupt:
            print("\nПрограмма завершена")
            self.listening_active = False
            if self.current_session:
                self.speak("Текущая сессия не завершена")

if __name__ == "__main__":
    # Создаем и запускаем оверлей в основном потоке
    overlay = OverlayWindow()
    
    # Создаем и запускаем систему распознавания в отдельном потоке
    recorder = TankStatsRecorder(overlay)
    
    # Создаем GUI для голосового контроля
    voice_gui = VoiceControlGUI(recorder)
    
    # Запускаем все компоненты в отдельных потоках
    recorder_thread = threading.Thread(target=recorder.run, daemon=True)
    recorder_thread.start()
    
    voice_gui_thread = threading.Thread(target=voice_gui.run, daemon=True)
    voice_gui_thread.start()
    
    # Запускаем оверлей (блокирующий вызов)
    overlay.run()