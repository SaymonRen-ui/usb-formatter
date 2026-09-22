import ctypes
import os
import re
import subprocess
import sys
import threading
import time


# ============================================================
# DISK FORMATTER
# Форматирование физических дисков через DISKPART
# ============================================================

DEFAULT_LABEL = "Local Disk"

# ============================================================
# ТЕМЫ (светлая / тёмная), выбор сохраняется рядом со скриптом
# ============================================================

THEMES = {
    "light": {
        "BG": "#EEF1F6",
        "CARD": "#FFFFFF",
        "FIELD": "#F8FAFF",
        "TEXT": "#1A1D29",
        "MUTED": "#6B7280",
        "ACCENT": "#4F7CFF",
        "ACCENT_HOVER": "#3B68E0",
        "ACCENT_PRESS": "#2F55C4",
        "ACCENT_DISABLED": "#C7D2FE",
        "BORDER": "#E3E7EF",
        "TRACK": "#E8ECF3",
        "DANGER": "#EF4444",
        "SUCCESS": "#10B981",
    },
    "dark": {
        "BG": "#14161D",
        "CARD": "#1E2230",
        "FIELD": "#262C3E",
        "TEXT": "#EDEFF5",
        "MUTED": "#9AA3B2",
        "ACCENT": "#5B8CFF",
        "ACCENT_HOVER": "#4A7BE8",
        "ACCENT_PRESS": "#3A68C8",
        "ACCENT_DISABLED": "#33405E",
        "BORDER": "#2C3347",
        "TRACK": "#2A3042",
        "DANGER": "#F87171",
        "SUCCESS": "#34D399",
    },
}

def _app_base_dir():
    # В exe (onefile) __file__ ведёт во временную папку — берём папку exe
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


THEME_FILE = os.path.join(_app_base_dir(), ".usb_formatter_theme")


def load_theme_name():
    try:
        with open(THEME_FILE, "r", encoding="utf-8") as f:
            name = f.read().strip().lower()
            if name in THEMES:
                return name
    except Exception:
        pass
    return "light"


def save_theme_name(name):
    try:
        with open(THEME_FILE, "w", encoding="utf-8") as f:
            f.write(name)
        return True
    except Exception:
        return False


def restart_app():
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        try:
            script = os.path.abspath(sys.argv[0])
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
            os.spawnl(os.P_NOWAIT, sys.executable, sys.executable, script, *sys.argv[1:])
        except Exception:
            pass


# ============================================================
# ПРОВЕРКА ПРАВ АДМИНИСТРАТОРА
# ============================================================

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# ============================================================
# ЗАПУСК DISKPART
# ============================================================

def _silent_kwargs():
    # Чтобы не всплывали чёрные окна консолей diskpart/powershell
    if os.name != "nt":
        return {}
    kw = {}
    try:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    except Exception:
        pass
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw["startupinfo"] = si
    except Exception:
        pass
    return kw


def run_diskpart(commands, timeout=3600):

    script = "\n".join(commands) + "\n"

    try:

        result = subprocess.run(
            ["diskpart"],
            input=script,
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            timeout=timeout,
            **_silent_kwargs()
        )

        return result.stdout + result.stderr

    except subprocess.TimeoutExpired:

        return (
            "ОШИБКА: DISKPART не ответил "
            "за отведённое время."
        )

    except Exception as e:

        return f"ОШИБКА запуска DISKPART: {e}"


def run_diskpart_live(commands, timeout=3600, on_line=None):

    # Тот же diskpart, но отдаёт строки по мере появления.
    # Нужно для прогресса: diskpart при полном форматировании
    # пишет проценты, при быстром — стадии.

    script = "\n".join(commands) + "\n"
    lines = []

    try:
        proc = subprocess.Popen(
            ["diskpart"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="cp866",
            errors="replace",
            bufsize=1,
            **_silent_kwargs()
        )

        try:
            proc.stdin.write(script)
            proc.stdin.close()
        except Exception:
            pass

        start = time.time()

        # Читаем построчно пока процесс жив
        while True:
            line = proc.stdout.readline()
            if line:
                lines.append(line)
                if on_line:
                    try:
                        on_line(line.rstrip("\r\n"))
                    except Exception:
                        pass
            # Проверка завершения
            ret = proc.poll()
            if ret is not None:
                # Добираем остаток
                try:
                    rest = proc.stdout.read()
                    if rest:
                        for rl in rest.splitlines():
                            lines.append(rl + "\n")
                            if on_line:
                                try:
                                    on_line(rl)
                                except Exception:
                                    pass
                except Exception:
                    pass
                break
            # Таймаут
            if time.time() - start > timeout:
                try:
                    proc.kill()
                except Exception:
                    pass
                lines.append("\nОШИБКА: DISKPART не ответил за отведённое время.\n")
                break

        try:
            proc.wait(timeout=5)
        except Exception:
            pass

        return "".join(lines)

    except Exception as e:
        return f"ОШИБКА запуска DISKPART: {e}"


def parse_format_percent(line):

    # diskpart пишет по-разному: "10 percent completed",
    # "Процент выполнен: 10", "10%". Ловим число 0-100
    # только в строках про проценты, чтобы не хватать номера дисков.

    low = line.lower()
    if ("percent" not in low and "процент" not in low and "%" not in line):
        return None

    m = re.search(r"(\d{1,3})\s*%", line)
    if m:
        try:
            v = int(m.group(1))
            if 0 <= v <= 100:
                return v
        except ValueError:
            pass

    m = re.search(r"(\d{1,3})", line)
    if m:
        try:
            v = int(m.group(1))
            if 0 <= v <= 100:
                return v
        except ValueError:
            pass

    return None


def set_automount(enable):

    # automount disable на время операции убирает то самое
    # системное окно Windows "Нужно отформатировать диск",
    # которое всплывает когда раздел создан но ещё не отформатирован.
    # После операции обязательно включаем обратно.

    try:
        return run_diskpart([
            "automount enable" if enable else "automount disable",
            "exit"
        ], timeout=15)
    except Exception:
        return ""


def get_free_letters():

    # Свободные буквы D..Z минус занятые томами.
    # A, B — legacy, C — система, их не предлагаем.

    used = set()

    try:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "(Get-CimInstance Win32_Volume | Where-Object { $_.DriveLetter } | "
            "Select-Object -ExpandProperty DriveLetter) -join ' '"
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_silent_kwargs()
        )
        for token in re.findall(r"([A-Za-z]):?", result.stdout):
            used.add(token.upper())
    except Exception:
        pass

    used.add("C")

    free = []
    for code in range(ord("D"), ord("Z") + 1):
        ch = chr(code)
        if ch not in used:
            free.append(ch)

    return free


def get_partitions(disk_number):

    # Текущие разделы диска: номер, буква, размер. Для окна "Дополнительно".

    try:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                f"Get-Partition -DiskNumber {int(disk_number)} "
                "-ErrorAction SilentlyContinue | "
                "ForEach-Object { "
                "$_.PartitionNumber.ToString() + '|' + "
                "$_.DriveLetter + '|' + "
                "$_.Size.ToString() "
                "}"
            )
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_silent_kwargs()
        )
        parts = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            num, letter, size = (line.split("|", 2) + ["", ""])[:3]
            if not num.strip().isdigit():
                continue
            try:
                size_gb = int(size.strip() or 0) / (1024 ** 3)
            except ValueError:
                size_gb = 0
            parts.append({
                "partition": num.strip(),
                "letter": letter.strip(),
                "size_gb": round(size_gb, 2)
            })
        return parts
    except Exception:
        return []


# ============================================================
# USB-ДИСКИ
# ============================================================

def get_usb_disks():

    usb_disks = set()

    try:

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Get-CimInstance Win32_DiskDrive | "
                "Where-Object { $_.InterfaceType -eq 'USB' } | "
                "Select-Object -ExpandProperty Index"
            )
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_silent_kwargs()
        )

        for line in result.stdout.splitlines():

            line = line.strip()

            if line.isdigit():
                usb_disks.add(int(line))

    except Exception:
        pass

    return usb_disks


# ============================================================
# МОДЕЛИ ДИСКОВ
# ============================================================

def get_disk_models():

    models = {}

    try:

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Get-CimInstance Win32_DiskDrive | "
                "ForEach-Object { "
                "$_.Index.ToString() + '|' + $_.Model "
                "}"
            )
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_silent_kwargs()
        )

        for line in result.stdout.splitlines():

            line = line.strip()

            if "|" not in line:
                continue

            number, model = line.split("|", 1)

            if number.strip().isdigit():

                models[int(number.strip())] = model.strip()

    except Exception:
        pass

    return models


# ============================================================
# СИСТЕМНЫЙ ДИСК (где Windows, обычно C:)
# Его форматировать ЗАПРЕЩЕНО всегда.
# ============================================================

def get_system_disk():

    try:

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Get-Partition -DriveLetter C "
                "-ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty DiskNumber"
            )
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_silent_kwargs()
        )

        text = result.stdout.strip()

        # Может вернуть несколько строк — берём первую цифру
        for line in text.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)

    except Exception:
        pass

    return None


# ============================================================
# ЖЕЛЕЗО ОДНИМ ЗАПРОСОМ (вместо трёх powershell подряд)
# ============================================================

def get_disk_hardware():

    usb_disks = set()
    models = {}
    system_disk = None

    try:

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Get-CimInstance Win32_DiskDrive | ForEach-Object { "
                "$_.Index.ToString() + '|' + $_.Model + '|' + $_.InterfaceType }; "
                "Write-Output '---SYS---'; "
                "Get-Partition -DriveLetter C "
                "-ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty DiskNumber"
            )
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_silent_kwargs()
        )

        in_sys = False

        for line in result.stdout.splitlines():

            s = line.strip()

            if s == "---SYS---":
                in_sys = True
                continue

            if in_sys:
                if s.isdigit():
                    system_disk = int(s)
            else:
                if "|" not in s:
                    continue
                num, model, iface = (s.split("|", 2) + ["", ""])[:3]
                if num.strip().isdigit():
                    idx = int(num.strip())
                    models[idx] = model.strip()
                    if iface.strip().upper() == "USB":
                        usb_disks.add(idx)

    except Exception:
        pass

    return usb_disks, models, system_disk


# Кэш последнего списка дисков: показываем мгновенно,
# свежее подгружаем фоном. seq отсекает устаревшие ответы.
_disk_cache = {"disks": None, "seq": 0}

# Замеры последнего опроса (для диагностики тормозов старта)
_last_load_times = {}


# ============================================================
# СПИСОК ДИСКОВ
# ============================================================

def get_disks():

    print()
    print("Получение списка дисков...")

    # diskpart и WMI идут параллельно — иначе их времена складываются
    out = {}
    times = {}

    def _dp():
        t0 = time.time()
        try:
            out["dp"] = run_diskpart([
                "list disk",
                "exit"
            ], timeout=15)
        except Exception as e:
            out["dp"] = f"ОШИБКА запуска DISKPART: {e}"
        times["diskpart"] = time.time() - t0

    def _hw():
        t0 = time.time()
        try:
            out["hw"] = get_disk_hardware()
        except Exception:
            out["hw"] = (set(), {}, None)
        times["hw"] = time.time() - t0

    t_all = time.time()
    t_dp = threading.Thread(target=_dp, daemon=True)
    t_hw = threading.Thread(target=_hw, daemon=True)
    t_dp.start()
    t_hw.start()
    t_dp.join(timeout=20)
    t_hw.join(timeout=15)
    times["total"] = time.time() - t_all

    try:
        _last_load_times.update(times)
    except Exception:
        pass

    output = out.get("dp", "")
    hw = out.get("hw", (set(), {}, None))
    usb_disks, models, system_disk = hw

    disks = []

    # Поддерживаем МБ / ГБ / ТБ, рус. и англ. вывод diskpart
    pattern = re.compile(
        r"(?:Диск|Disk)\s+(\d+)"
        r".*?"
        r"([0-9]+(?:[.,][0-9]+)?)\s*"
        r"(Мбайт|Gбайт|Tбайт|МБ|ГБ|ТБ|MB|GB|TB)",
        re.IGNORECASE
    )

    for line in output.splitlines():

        match = pattern.search(line)

        if not match:
            continue

        number = int(match.group(1))

        try:
            size_value = float(match.group(2).replace(",", "."))
        except ValueError:
            continue

        unit = match.group(3).upper()

        # Нормализуем к ГБ для проверок (FAT32 и т.д.)
        if unit in ("MB", "МБ", "МБАЙТ"):
            size_gb = size_value / 1024.0
            size_str = f"{size_value:g} MB"
        elif unit in ("TB", "ТБ", "ТБАЙТ"):
            size_gb = size_value * 1024.0
            size_str = f"{size_value:g} TB"
        else:
            size_gb = size_value
            size_str = f"{size_value:g} GB"

        is_usb = number in usb_disks
        is_system = (system_disk is not None and number == system_disk)

        if is_system:
            device_type = "[СИСТЕМНЫЙ]"
        elif is_usb:
            device_type = "[USB]"
        else:
            device_type = "[HDD/SSD]"

        model = models.get(
            number,
            "Модель неизвестна"
        )

        disks.append({
            "number": number,
            "size": size_str,
            "size_gb": size_gb,
            "type": device_type,
            "model": model,
            "is_usb": is_usb,
            "is_system": is_system
        })

    return disks


# ============================================================
# ПОДРОБНАЯ ИНФОРМАЦИЯ
# ============================================================

def get_disk_detail(number):

    return run_diskpart([
        f"select disk {number}",
        "detail disk",
        "exit"
    ], timeout=30)


# ============================================================
# YES
# ============================================================

def is_yes(text):

    text = text.strip().lower()

    return text in {
        "y",
        "yes",
        "н",
        "да",
        "д",
        "lf"
    }


# ============================================================
# NO
# ============================================================

def is_no(text):

    text = text.strip().lower()

    return text in {
        "n",
        "no",
        "т",
        "нет"
    }


# ============================================================
# YES / NO
# ============================================================

def ask_yes_no(question, default_yes=True):

    print()
    print(question)

    print()
    print("[Y / Н] YES")
    print("[N / Т] NO")

    if default_yes:

        print()
        print("Enter = YES")

    else:

        print()
        print("Enter = NO")

    while True:

        choice = input(
            "\nВаш выбор: "
        ).strip().lower()

        if choice == "":

            return default_yes

        if is_yes(choice):

            return True

        if is_no(choice):

            return False

        print()
        print(
            "Введите Y/Н для YES "
            "или N/Т для NO."
        )


# ============================================================
# ВЫБОР ДИСКА
# ============================================================

def choose_disk(disks):

    print()
    print("=" * 90)
    print("                              СПИСОК ДИСКОВ")
    print("=" * 90)

    print()

    for disk in disks:

        lock = "  [ЗАБЛОКИРОВАН]" if disk.get("is_system") else ""

        print(
            f"[{disk['number']}] "
            f"{disk['type']:12} "
            f"{disk['size']:>8}   "
            f"{disk['model']}{lock}"
        )

    print()
    print("[R / К] Обновить список")
    print("[Q / Й] Выход")

    while True:

        choice = input(
            "\nВыберите номер диска: "
        ).strip().lower()

        if choice in ("q", "й"):

            return None

        if choice in ("r", "к"):

            return "refresh"

        if choice.isdigit():

            number = int(choice)

            for disk in disks:

                if disk["number"] == number:

                    if disk.get("is_system"):
                        print()
                        print(
                            "ЗАПРЕЩЕНО: это системный диск Windows. "
                            "Выбери USB-флешку."
                        )
                        break

                    return number

        print(
            "Неверный выбор."
        )


# ============================================================
# ФАЙЛОВАЯ СИСТЕМА
# ============================================================

def choose_filesystem():

    print()
    print("=" * 70)
    print("                         ФАЙЛОВАЯ СИСТЕМА")
    print("=" * 70)

    print()

    print("[1] FAT32")
    print("[2] exFAT")
    print("[3] NTFS")

    print()
    print("[Q / Й] Отмена")

    while True:

        choice = input(
            "\nВыберите файловую систему: "
        ).strip().lower()

        if choice == "1":

            return "FAT32"

        if choice == "2":

            return "EXFAT"

        if choice == "3":

            return "NTFS"

        if choice in ("q", "й"):

            return None

        print(
            "Неверный выбор."
        )


# ============================================================
# ИМЯ ТОМA
# ============================================================

def choose_label():

    print()

    label = input(
        f"Имя тома (Enter = {DEFAULT_LABEL}): "
    ).strip()

    if not label:

        return DEFAULT_LABEL

    label = re.sub(
        r'[^A-Za-zА-Яа-я0-9 _.-]',
        "",
        label
    )

    label = label[:32]

    if not label:

        return DEFAULT_LABEL

    return label


# ============================================================
# БЫСТРОЕ / ПОЛНОЕ
# ============================================================

def choose_format_mode():

    print()
    print("=" * 70)
    print("                       ТИП ФОРМАТИРОВАНИЯ")
    print("=" * 70)

    print()

    print(
        "[Y / Н] YES — быстрое форматирование"
    )

    print(
        "[N / Т] NO  — полное форматирование"
    )

    print()
    print(
        "Enter = YES — быстрое"
    )

    while True:

        choice = input(
            "\nВаш выбор: "
        ).strip().lower()

        if choice == "":

            return True

        if is_yes(choice):

            return True

        if is_no(choice):

            return False

        print()
        print(
            "Введите Y/Н или N/Т."
        )


# ============================================================
# ПРОВЕРКА ТОМA ЧЕРЕЗ POWERSHELL
# ============================================================

def get_volume_info(disk_number):

    try:

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$p = Get-Partition "
                f"-DiskNumber {disk_number} "
                "-ErrorAction SilentlyContinue | "
                "Where-Object { "
                "$_.Type -ne 'Reserved' "
                "} | "
                "Select-Object -First 1; "
                "if ($p) { "
                "$v = $p | Get-Volume "
                "-ErrorAction SilentlyContinue; "
                "if ($v) { "
                "Write-Output "
                "($p.PartitionNumber.ToString() + '|' + "
                "$v.DriveLetter + '|' + "
                "$v.FileSystem + '|' + "
                "$v.FileSystemLabel) "
                "} "
                "}"
            )
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **_silent_kwargs()
        )

        text = result.stdout.strip()

        if not text:

            return None

        parts = text.split("|")

        if len(parts) < 4:

            return None

        return {
            "partition": parts[0],
            "letter": parts[1],
            "filesystem": parts[2],
            "label": parts[3]
        }

    except Exception:

        return None


# ============================================================
# БЫСТРОЕ ФОРМАТИРОВАНИЕ
# ============================================================

def verify_disk_selected(number):

    # Отдельный запуск: проверяем что диск N реально выбирается,
    # до того как делать clean. Защита от wipe не того диска.

    output = run_diskpart([
        f"select disk {number}",
        "detail disk",
        "exit"
    ], timeout=30)

    text = output.lower()

    # diskpart пишет "Выбран диск N" / "Disk N is now the selected disk"
    # + detail disk должен показать информацию без "не найден"
    selected_ok = bool(re.search(
        r"(выбран\s+диск\s+" + str(number) + r")"
        r"|(disk\s+" + str(number) + r"\s+is\s+now.*selected)",
        output,
        re.IGNORECASE
    ))

    # Если diskpart ругнулся — не продолжаем
    has_error = bool(re.search(
        r"не найден|неверн|недопустим|no\s+such|invalid|error",
        output,
        re.IGNORECASE
    )) and not selected_ok

    if has_error:
        return False, output

    return selected_ok, output


def format_disk(
    number,
    filesystem,
    label,
    quick,
    size_gb=None,
    letter=None,
    progress_callback=None
):

    safe_label = label.replace(
        '"',
        ""
    )

    filesystem = filesystem.upper()
    if filesystem not in ("FAT32", "EXFAT", "NTFS"):
        return False, f"Неизвестная файловая система: {filesystem}", 0.0

    # --------------------------------------------------------
    # БЛОКИРОВКИ БЕЗОПАСНОСТИ
    # --------------------------------------------------------

    # 0. Системный диск — запрет всегда (даже если скрыт из списка)
    try:
        sys_disk = get_system_disk()
    except Exception:
        sys_disk = None

    if sys_disk is not None and number == sys_disk:
        return False, "ЗАПРЕЩЕНО: это системный диск Windows (C:).", 0.0

    # 1. FAT32 больше 32 ГБ diskpart не сделает
    if filesystem == "FAT32" and size_gb is not None and size_gb > 32:
        return (
            False,
            "FAT32 через DISKPART работает только до 32 ГБ. "
            "Выбери exFAT или NTFS.",
            0.0
        )

    # 2. Проверяем что диск реально выбирается — ДО clean
    ok, check_output = verify_disk_selected(number)

    if not ok:
        return (
            False,
            "Не удалось подтвердить выбор диска. Форматирование отменено "
            "для безопасности.\n\nВывод DISKPART:\n" + check_output,
            0.0
        )

    # --------------------------------------------------------
    # ВТОРОЙ ЗАПУСК DISKPART — только после проверки
    # --------------------------------------------------------

    # --------------------------------------------------------
    # ВТОРОЙ ЗАПУСК DISKPART — только после проверки
    # automount disable убирает системное окно Windows
    # "Нужно отформатировать диск" в момент между
    # create partition и format. rescan в конце убран —
    # он тоже провоцировал повторный опрос и окно.
    # --------------------------------------------------------

    # Явная буква, если выбрана в "Дополнительно" / GUI
    if letter:
        letter = str(letter).strip().upper().replace(":", "")
        if not re.fullmatch(r"[D-Z]", letter or ""):
            return False, f"Некорректная буква диска: {letter}", 0.0
        assign_cmd = f"assign letter={letter}"
    else:
        assign_cmd = "assign"

    commands = [
        f"select disk {number}",
        "clean",
        "create partition primary",
        "select partition 1",
    ]

    if quick:
        commands.append(
            f'format fs={filesystem} '
            f'quick label="{safe_label}"'
        )
    else:
        commands.append(
            f'format fs={filesystem} '
            f'label="{safe_label}"'
        )

    commands.extend([
        assign_cmd,
        "exit"
    ])

    # --------------------------------------------------------
    # ЗАПУСК (с живым прогрессом)
    # --------------------------------------------------------

    print()
    print("Выполнение DISKPART...")

    print()

    if progress_callback:
        try:
            progress_callback(2, "Проверка и подготовка...")
        except Exception:
            pass

    # Глушим автомонт чтобы Windows не показывала окно форматирования
    try:
        set_automount(False)
    except Exception:
        pass

    start_time = time.time()

    def _on_line(line):
        if not progress_callback:
            return
        low = line.lower()
        pct = parse_format_percent(line)
        try:
            if pct is not None:
                # Форматирование занимает ~50%..95% всей операции
                mapped = 50 + int(pct * 0.45)
                progress_callback(mapped, f"Форматирование... {pct}%")
            elif "clean" in low or "очистка" in low or "очищен" in low:
                progress_callback(20, "Очистка таблицы разделов...")
            elif "create partition" in low or "создан раздел" in low or "создание раздела" in low:
                progress_callback(40, "Создание раздела...")
            elif "assign" in low or "назначен" in low:
                progress_callback(95, "Назначение буквы...")
            elif "выбран диск" in low or "selected disk" in low:
                progress_callback(10, "Диск выбран...")
        except Exception:
            pass

    try:
        result = run_diskpart_live(
            commands,
            timeout=3600,
            on_line=_on_line
        )
    finally:
        # Возвращаем автомонт всегда, даже при ошибке
        try:
            set_automount(True)
        except Exception:
            pass

    elapsed = time.time() - start_time

    if progress_callback:
        try:
            progress_callback(100, "Проверка результата...")
        except Exception:
            pass

    # --------------------------------------------------------
    # ПРОВЕРКА ОШИБОК
    # --------------------------------------------------------

    if re.search(
        r"error|ошибка|failed|не удалось",
        result,
        re.IGNORECASE
    ):

        return False, result, elapsed

    # --------------------------------------------------------
    # ДАЁМ WINDOWS ОБНОВИТЬ ИНФОРМАЦИЮ
    #
    # Не ждём 2-3 секунды без причины.
    # Проверяем сразу, затем при необходимости
    # делаем ещё несколько быстрых попыток.
    # --------------------------------------------------------

    volume = None

    for _ in range(5):

        volume = get_volume_info(
            number
        )

        if volume is not None:

            if volume["letter"]:

                break

        time.sleep(0.2)

    # --------------------------------------------------------
    # НЕ НАШЛИ ТОМ
    # --------------------------------------------------------

    if volume is None:

        return False, result, elapsed

    if not volume["letter"]:

        return False, result, elapsed

    # --------------------------------------------------------
    # ПРОВЕРЯЕМ ФАЙЛОВУЮ СИСТЕМУ
    # --------------------------------------------------------

    if volume["filesystem"].upper() != filesystem.upper():

        return False, result, elapsed

    # --------------------------------------------------------
    # УСПЕХ
    # --------------------------------------------------------

    return True, volume, elapsed


# ============================================================
# РАСШИРЕННОЕ ФОРМАТИРОВАНИЕ — несколько томов, буквы, MBR/GPT
# partitions: [{"size_mb": int|None(None=остаток), "fs": "EXFAT",
#               "label": "USB1", "letter": "E"|None(auto)}]
# style: None/"MBR"/"GPT", clean_all: True=clean all (долго, надёжно)
# ============================================================

def format_disk_advanced(
    number,
    partitions,
    style=None,
    clean_all=False,
    progress_callback=None
):

    # --- Проверки ---
    try:
        sys_disk = get_system_disk()
    except Exception:
        sys_disk = None

    if sys_disk is not None and number == sys_disk:
        return False, "ЗАПРЕЩЕНО: это системный диск Windows (C:).", 0.0

    if not partitions or len(partitions) == 0:
        return False, "Нет разделов для создания.", 0.0

    if len(partitions) > 4:
        return False, "Максимум 4 раздела для простоты.", 0.0

    cleaned = []
    seen_letters = set()

    for i, p in enumerate(partitions):
        fs = str(p.get("fs", "EXFAT")).upper()
        if fs not in ("FAT32", "EXFAT", "NTFS"):
            return False, f"Раздел {i + 1}: неизвестная ФС {fs}", 0.0

        size_mb = p.get("size_mb")
        if size_mb is not None:
            try:
                size_mb = int(size_mb)
            except (ValueError, TypeError):
                return False, f"Раздел {i + 1}: плохой размер.", 0.0
            if size_mb < 100:
                return False, f"Раздел {i + 1}: минимум 100 МБ.", 0.0

        label = re.sub(r'[^A-Za-zА-Яа-я0-9 _.-]', "", str(p.get("label", f"USB{i + 1}")))[:32] or f"USB{i + 1}"

        letter = p.get("letter")
        if letter:
            letter = str(letter).strip().upper().replace(":", "")
            if not re.fullmatch(r"[D-Z]", letter):
                return False, f"Раздел {i + 1}: плохая буква {letter}", 0.0
            if letter in seen_letters:
                return False, f"Буква {letter} повторяется.", 0.0
            seen_letters.add(letter)

        # FAT32 больше 32 ГБ нельзя
        # (точный размер всего диска тут не знаем, проверим грубо по size_mb)
        if fs == "FAT32" and size_mb is not None and size_mb > 32 * 1024:
            return False, f"Раздел {i + 1}: FAT32 только до 32 ГБ.", 0.0

        cleaned.append({
            "size_mb": size_mb,
            "fs": fs,
            "label": label.replace('"', ""),
            "letter": letter
        })

    # Только последний раздел может быть "остаток"
    for j in range(len(cleaned) - 1):
        if cleaned[j]["size_mb"] is None:
            return False, "Только последний раздел может быть 'остаток'.", 0.0

    ok, check_output = verify_disk_selected(number)
    if not ok:
        return False, "Не удалось подтвердить выбор диска.\n\n" + check_output, 0.0

    # --- Скрипт ---
    commands = [f"select disk {number}"]

    commands.append("clean all" if clean_all else "clean")

    if style == "MBR":
        commands.append("convert mbr")
    elif style == "GPT":
        commands.append("convert gpt")

    for i, p in enumerate(cleaned):
        if p["size_mb"] is None:
            commands.append("create partition primary")
        else:
            commands.append(f"create partition primary size={p['size_mb']}")
        commands.append(f"select partition {i + 1}")
        commands.append(f'format fs={p["fs"]} quick label="{p["label"]}"')
        if p["letter"]:
            commands.append(f"assign letter={p['letter']}")
        else:
            commands.append("assign")

    commands.append("exit")

    if progress_callback:
        try:
            progress_callback(2, "Подготовка...")
        except Exception:
            pass

    try:
        set_automount(False)
    except Exception:
        pass

    start_time = time.time()

    def _on_line(line):
        if not progress_callback:
            return
        pct = parse_format_percent(line)
        try:
            if pct is not None:
                progress_callback(pct, f"Форматирование... {pct}%")
            else:
                low = line.lower()
                if "clean" in low or "очистка" in low:
                    progress_callback(15, "Очистка...")
                elif "convert" in low or "преобразован" in low:
                    progress_callback(25, "Стиль разделов...")
                elif "create partition" in low or "создан раздел" in low:
                    progress_callback(40, "Создание томов...")
                elif "assign" in low or "назначен" in low:
                    progress_callback(90, "Назначение букв...")
        except Exception:
            pass

    try:
        result = run_diskpart_live(commands, timeout=3600, on_line=_on_line)
    finally:
        try:
            set_automount(True)
        except Exception:
            pass

    elapsed = time.time() - start_time

    if re.search(r"error|ошибка|failed|не удалось", result, re.IGNORECASE):
        return False, result, elapsed

    if progress_callback:
        try:
            progress_callback(100, "Готово")
        except Exception:
            pass

    return True, result, elapsed


# ============================================================
# MAIN
# ============================================================

def main():

    os.system(
        "title Disk Formatter - DISKPART"
    )

    print("=" * 90)
    print("                              DISK FORMATTER")
    print("                         Форматирование через DISKPART")
    print("=" * 90)

    print()
    print("ВНИМАНИЕ!")
    print()
    print(
        "Программа может полностью удалить данные"
    )
    print(
        "с выбранного физического диска."
    )

    print()

    # --------------------------------------------------------
    # WINDOWS
    # --------------------------------------------------------

    if os.name != "nt":

        print(
            "ОШИБКА: программа предназначена "
            "только для Windows."
        )

        input(
            "\nНажмите Enter..."
        )

        return

    # --------------------------------------------------------
    # АДМИНИСТРАТОР
    # --------------------------------------------------------

    if not is_admin():

        print(
            "Программа запущена без прав администратора."
        )

        print()
        print(
            "Перезапуск от имени администратора..."
        )

        try:

            script = os.path.abspath(
                sys.argv[0]
            )

            params = " ".join(
                f'"{arg}"'
                for arg in sys.argv[1:]
            )

            ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                sys.executable,
                f'"{script}" {params}',
                None,
                1
            )

        except Exception as e:

            print()
            print(
                "Ошибка:"
            )

            print(e)

            input(
                "\nНажмите Enter..."
            )

        return

    print(
        "Права администратора: OK"
    )

    # ========================================================
    # ГЛАВНЫЙ ЦИКЛ
    # ========================================================

    while True:

        disks = get_disks()

        if not disks:

            print()
            print(
                "Не удалось получить список дисков."
            )

            input(
                "\nНажмите Enter для выхода..."
            )

            return

        # ----------------------------------------------------
        # ВЫБОР ДИСКА
        # ----------------------------------------------------

        choice = choose_disk(
            disks
        )

        if choice is None:

            print(
                "\nВыход."
            )

            return

        if choice == "refresh":

            os.system("cls")

            continue

        number = choice

        selected_disk = None

        for disk in disks:

            if disk["number"] == number:

                selected_disk = disk

                break

        if selected_disk is None:

            continue

        # ----------------------------------------------------
        # ИНФОРМАЦИЯ
        # ----------------------------------------------------

        print()
        print("=" * 90)
        print("                           ВЫБРАННЫЙ ДИСК")
        print("=" * 90)

        print()

        print(
            "Диск   :",
            number
        )

        print(
            "Тип    :",
            selected_disk["type"]
        )

        print(
            "Размер :",
            selected_disk["size"]
        )

        print(
            "Модель :",
            selected_disk["model"]
        )

        # ----------------------------------------------------
        # ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ
        # Enter = NO
        # ----------------------------------------------------

        extra = ask_yes_no(
            "Показать дополнительную информацию о диске?",
            default_yes=False
        )

        if extra:

            print()
            print(
                "Получение подробной информации..."
            )

            print()

            print(
                get_disk_detail(
                    number
                )
            )

        # ----------------------------------------------------
        # ПЕРВОЕ ПОДТВЕРЖДЕНИЕ
        # Enter = YES
        # ----------------------------------------------------

        print()
        print("!" * 90)
        print("                              ВНИМАНИЕ")
        print("!" * 90)

        print()

        print(
            "ВСЕ ДАННЫЕ НА ЭТОМ ДИСКЕ "
            "БУДУТ УДАЛЕНЫ!"
        )

        confirm = ask_yes_no(
            "Продолжить?",
            default_yes=True
        )

        if not confirm:

            print()
            print(
                "Операция отменена."
            )

            input(
                "\nНажмите Enter..."
            )

            os.system("cls")

            continue

        # ----------------------------------------------------
        # ФАЙЛОВАЯ СИСТЕМА
        # ----------------------------------------------------

        filesystem = choose_filesystem()

        if filesystem is None:

            os.system("cls")

            continue

        # ----------------------------------------------------
        # ИМЯ ТОМA
        # ----------------------------------------------------

        label = choose_label()

        # ----------------------------------------------------
        # БЫСТРОЕ / ПОЛНОЕ
        # ----------------------------------------------------

        quick = choose_format_mode()

        # ----------------------------------------------------
        # ФИНАЛЬНОЕ ПОДТВЕРЖДЕНИЕ
        # Enter = YES
        # ----------------------------------------------------

        print()
        print("=" * 90)
        print("                         ПОСЛЕДНЕЕ ПРЕДУПРЕЖДЕНИЕ")
        print("=" * 90)

        print()

        print(
            "Диск             :",
            number
        )

        print(
            "Тип              :",
            selected_disk["type"]
        )

        print(
            "Размер           :",
            selected_disk["size"]
        )

        print(
            "Модель           :",
            selected_disk["model"]
        )

        print(
            "Файловая система :",
            filesystem
        )

        print(
            "Имя тома         :",
            label
        )

        if quick:

            print(
                "Форматирование   : БЫСТРОЕ"
            )

        else:

            print(
                "Форматирование   : ПОЛНОЕ"
            )

        print()

        print(
            "ВСЕ ДАННЫЕ НА ЭТОМ ДИСКЕ "
            "БУДУТ УДАЛЕНЫ!"
        )

        final_confirm = ask_yes_no(
            "Начать форматирование?",
            default_yes=True
        )

        if not final_confirm:

            print()
            print(
                "Операция отменена."
            )

            input(
                "\nНажмите Enter..."
            )

            os.system("cls")

            continue

        # ----------------------------------------------------
        # ФОРМАТИРОВАНИЕ
        # ----------------------------------------------------

        print()
        print("=" * 90)
        print("                         ФОРМАТИРОВАНИЕ")
        print("=" * 90)

        print()

        if quick:

            print(
                "Режим: БЫСТРОЕ"
            )

        else:

            print(
                "Режим: ПОЛНОЕ"
            )

        print()

        success, result, elapsed = format_disk(
            number,
            filesystem,
            label,
            quick,
            size_gb=selected_disk.get("size_gb")
        )

        # ----------------------------------------------------
        # УСПЕХ
        # ----------------------------------------------------

        if success:

            print()
            print("=" * 90)
            print(
                "                 ФОРМАТИРОВАНИЕ УСПЕШНО"
            )
            print("=" * 90)

            print()

            print(
                "Буква диска       :",
                result["letter"] + ":"
            )

            print(
                "Файловая система  :",
                result["filesystem"]
            )

            print(
                "Имя тома          :",
                result["label"]
            )

            print()

            print(
                f"Время операции    : {elapsed:.1f} сек."
            )

            print()

            print(
                "Том успешно создан "
                "и доступен Windows."
            )

        # ----------------------------------------------------
        # ОШИБКА
        # ----------------------------------------------------

        else:

            print()
            print("=" * 90)
            print(
                "                  ОШИБКА ФОРМАТИРОВАНИЯ"
            )
            print("=" * 90)

            print()

            print(
                "DISKPART / Windows вернули:"
            )

            print()

            print(
                result
            )

            print()

            print(
                "Флешка НЕ была признана "
                "полностью готовым томом."
            )

        print()

        input(
            "Нажмите Enter для возврата к списку..."
        )

        os.system("cls")


# ============================================================
# GUI — простой режим "одной кнопкой" + безопасность
# ============================================================

def restart_as_admin():

    try:
        if getattr(sys, "frozen", False):
            # Запуск из exe: повышаем сам exe
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
        else:
            script = os.path.abspath(sys.argv[0])
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{script}" {params}', None, 1
            )
        return True
    except Exception:
        return False


def ensure_admin():
    # Всегда запускаемся с правами администратора:
    # без них — UAC-запрос и перезапуск, текущий процесс завершается.
    if os.name != "nt":
        return True
    try:
        if is_admin():
            return True
    except Exception:
        pass
    try:
        restart_as_admin()
    finally:
        try:
            sys.exit(0)
        except SystemExit:
            raise
        except Exception:
            pass
    return False


def _gui_session():

    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox

    # Флаг пересборки окна (быстрое переключение темы без перезапуска процесса)
    _rebuild = {"flag": False}

    root = tk.Tk()
    root.title("USB Formatter — просто и безопасно")
    # Высота подстраивается под экран, чтобы низ не уходил за границы
    try:
        _sw = root.winfo_screenwidth()
        _sh = root.winfo_screenheight()
    except Exception:
        _sw, _sh = (1280, 800)
    _win_h = min(700, _sh - 60)
    _win_w = 660
    root.geometry(f"{_win_w}x{_win_h}")
    root.minsize(620, 520)
    root.resizable(False, True)
    try:
        _x = max(0, (_sw - _win_w) // 2)
        _y = max(0, (_sh - _win_h) // 2)
        root.geometry(f"{_win_w}x{_win_h}+{_x}+{_y}")
    except Exception:
        pass

    # ---------- Палитра из выбранной темы (п.8) ----------
    _theme_name = load_theme_name()
    _pal = THEMES.get(_theme_name, THEMES["light"])
    BG = _pal["BG"]
    CARD = _pal["CARD"]
    FIELD = _pal["FIELD"]
    TEXT = _pal["TEXT"]
    MUTED = _pal["MUTED"]
    ACCENT = _pal["ACCENT"]
    ACCENT_HOVER = _pal["ACCENT_HOVER"]
    ACCENT_PRESS = _pal["ACCENT_PRESS"]
    ACCENT_DISABLED = _pal["ACCENT_DISABLED"]
    BORDER = _pal["BORDER"]
    TRACK = _pal["TRACK"]
    DANGER = _pal["DANGER"]
    SUCCESS = _pal["SUCCESS"]

    root.configure(bg=BG)
    # Плавное появление окна
    try:
        root.attributes("-alpha", 0.0)
    except Exception:
        pass

    def _fade_in(widget, step=0):
        try:
            widget.attributes("-alpha", min(1.0, step / 10.0))
            if step < 10:
                widget.after(18, lambda: _fade_in(widget, step + 1))
        except Exception:
            pass

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"),
                    background=CARD, foreground=TEXT)
    style.configure("Sub.TLabel", font=("Segoe UI", 9),
                    background=CARD, foreground=MUTED)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Root.TFrame", background=BG)
    style.configure("Card.TLabelframe", background=CARD, borderwidth=0,
                    relief="flat")
    style.configure("Card.TLabelframe.Label", background=CARD,
                    foreground=MUTED, font=("Segoe UI", 9, "bold"))
    style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                    font=("Segoe UI", 9))
    style.configure("TRadiobutton", background=CARD, foreground=TEXT,
                    font=("Segoe UI", 9))
    style.configure("TCombobox", fieldbackground=FIELD,
                    background=FIELD, foreground=TEXT,
                    arrowcolor=ACCENT, borderwidth=1, relief="flat")
    style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT)

    # Скруглённая кнопка на Canvas с анимацией наведения/нажатия
    class RoundedButton(tk.Canvas):
        def __init__(self, parent, text, command=None, bg_color=ACCENT,
                     fg_color="#FFFFFF", width=200, height=40, radius=14,
                     font=("Segoe UI", 11, "bold"), secondary=False):
            super().__init__(parent, width=width, height=height,
                             highlightthickness=0, bd=0, bg=parent.cget("bg")
                             if "bg" in parent.keys() else BG)
            self._bg = bg_color
            self._fg = fg_color
            self._cmd = command
            self._radius = radius
            self._secondary = secondary
            self._normal_bg = CARD if secondary else bg_color
            self._hover_bg = FIELD if secondary else ACCENT_HOVER
            self._press_bg = BORDER if secondary else ACCENT_PRESS
            self._cur = self._normal_bg
            self._target = self._normal_bg
            self._text = text
            self._font = font
            self._enabled = True
            self.bind("<Enter>", lambda e: self._set_target(self._hover_bg))
            self.bind("<Leave>", lambda e: self._set_target(self._normal_bg))
            self.bind("<ButtonPress-1>", self._on_press)
            self.bind("<ButtonRelease-1>", self._on_release)
            self._anim_step()

        def _round_rect(self, x1, y1, x2, y2, r, **kw):
            points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                      x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                      x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
            return self.create_polygon(points, smooth=True, **kw)

        def _redraw(self):
            self.delete("all")
            w = int(self.cget("width"))
            h = int(self.cget("height"))
            fill = self._cur if self._enabled else ACCENT_DISABLED
            outline = ACCENT if self._secondary else fill
            self._round_rect(2, 2, w - 2, h - 2, self._radius,
                             fill=fill, outline=outline, width=1 if self._secondary else 0)
            self.create_text(w // 2, h // 2, text=self._text,
                             fill=(ACCENT if self._secondary else self._fg),
                             font=self._font)

        def _mix(self, a, b, t):
            # Плавный переход цвета a->b, t 0..1
            def _h(c):
                c = c.lstrip("#")
                return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
            ca, cb = _h(a), _h(b)
            cc = tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
            return "#%02x%02x%02x" % cc

        def _anim_step(self):
            if self._cur != self._target:
                try:
                    self._cur = self._mix(self._cur, self._target, 0.35)
                except Exception:
                    self._cur = self._target
                self._redraw()
            elif not hasattr(self, "_drawn"):
                self._redraw()
                self._drawn = True
            self.after(16, self._anim_step)

        def _set_target(self, c):
            if self._enabled:
                self._target = c

        def _on_press(self, e):
            if self._enabled:
                self._target = self._press_bg

        def _on_release(self, e):
            if not self._enabled:
                return
            self._target = self._hover_bg
            # Клик только если отпустили внутри
            try:
                if 0 <= e.x <= int(self.cget("width")) and 0 <= e.y <= int(self.cget("height")):
                    if self._cmd:
                        self._cmd()
            except Exception:
                pass

        def set_enabled(self, enabled):
            self._enabled = enabled
            self._target = self._normal_bg if enabled else ACCENT_DISABLED
            self._redraw()

        def set_text(self, text):
            self._text = text
            self._redraw()

        def set_command(self, cmd):
            self._cmd = cmd

    # Современный прогресс: скруглённая полоса на Canvas
    # ВАЖНО: не использовать self._w — это служебное имя виджета в tkinter!
    # Полоса тянется на всю ширину родителя.
    class ModernProgress(tk.Canvas):
        def __init__(self, parent, width=400, height=14):
            super().__init__(parent, width=width, height=height,
                             highlightthickness=0, bd=0,
                             bg=parent.cget("bg") if "bg" in parent.keys() else CARD)
            self._host = parent
            self._bar_w = width
            self._bar_h = height
            self._val = 0
            self._shown = 0.0
            self._draw()
            self._tick()
            try:
                parent.bind("<Configure>", self._on_host_resize, add="+")
            except Exception:
                pass

        def _on_host_resize(self, e=None):
            try:
                pw = self._host.winfo_width()
                if pw > 50 and abs(pw - self._bar_w) > 2:
                    self._bar_w = pw
                    self.config(width=pw)
                    self._draw()
            except Exception:
                pass

        def _rr(self, x1, y1, x2, y2, r, **kw):
            pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                   x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                   x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
            return self.create_polygon(pts, smooth=True, **kw)

        def _draw(self):
            self.delete("all")
            self._rr(0, 0, self._bar_w, self._bar_h, 7, fill=TRACK, outline="")
            fw = max(0, min(self._bar_w, self._bar_w * self._shown / 100.0))
            if fw > 1:
                self._rr(0, 0, fw, self._bar_h, 7, fill=ACCENT, outline="")

        def _tick(self):
            # Плавно догоняем целевое значение
            if abs(self._shown - self._val) > 0.3:
                self._shown += (self._val - self._shown) * 0.2
                self._draw()
            self.after(16, self._tick)

        def set(self, v):
            try:
                self._val = max(0, min(100, int(v)))
            except Exception:
                self._val = 0

    def _round_poly(cv, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    class ModernRadio(tk.Frame):
        # Свой радиокружок вместо стандартного
        def __init__(self, parent, text, value, variable, command=None, bg=None):
            super().__init__(parent, bg=bg or CARD)
            self._var = variable
            self._val = value
            self._cmd = command
            self._bg = bg or CARD
            self._cv = tk.Canvas(self, width=20, height=20, highlightthickness=0, bd=0, bg=self._bg)
            self._cv.pack(side="left")
            self._lb = tk.Label(self, text=text, font=("Segoe UI", 9), bg=self._bg, fg=TEXT)
            self._lb.pack(side="left", padx=(2, 0))
            for ch in (self, self._cv, self._lb):
                ch.bind("<Button-1>", self._pick)
            try:
                self._var.trace_add("write", lambda *a: self._draw())
            except Exception:
                pass
            self._draw()

        def _pick(self, e=None):
            self._var.set(self._val)
            if self._cmd:
                try:
                    self._cmd()
                except Exception:
                    pass

        def _draw(self):
            try:
                self._cv.delete("all")
                on = (self._var.get() == self._val)
                self._cv.create_oval(3, 3, 17, 17, outline=ACCENT if on else BORDER, width=2)
                if on:
                    self._cv.create_oval(7, 7, 13, 13, fill=ACCENT, outline="")
            except Exception:
                pass

    class ModernCheck(tk.Frame):
        # Свой чекбокс вместо стандартного
        def __init__(self, parent, text, variable, command=None, bg=None, wraplength=520):
            super().__init__(parent, bg=bg or CARD)
            self._var = variable
            self._cmd = command
            self._bg = bg or CARD
            self._cv = tk.Canvas(self, width=22, height=22, highlightthickness=0, bd=0, bg=self._bg)
            self._cv.pack(side="left")
            self._lb = tk.Label(self, text=text, font=("Segoe UI", 9), bg=self._bg, fg=TEXT,
                                wraplength=wraplength, justify="left")
            self._lb.pack(side="left", padx=(3, 0))
            for ch in (self, self._cv, self._lb):
                ch.bind("<Button-1>", self._toggle)
            try:
                self._var.trace_add("write", lambda *a: self._draw())
            except Exception:
                pass
            self._draw()

        def _toggle(self, e=None):
            try:
                self._var.set(not bool(self._var.get()))
            except Exception:
                pass
            if self._cmd:
                try:
                    self._cmd()
                except Exception:
                    pass

        def _draw(self):
            try:
                self._cv.delete("all")
                on = bool(self._var.get())
                _round_poly(self._cv, 3, 3, 19, 19, 5,
                            fill=ACCENT if on else FIELD,
                            outline=ACCENT if on else BORDER, width=2)
                if on:
                    self._cv.create_line(7, 11, 10, 14, fill="#FFFFFF", width=2)
                    self._cv.create_line(10, 14, 15, 7, fill="#FFFFFF", width=2)
            except Exception:
                pass

    def modern_entry(parent, textvariable, width=12, bg=None):
        e = tk.Entry(parent, textvariable=textvariable, width=width,
                     font=("Segoe UI", 9), bg=FIELD, fg=TEXT, relief="flat",
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT, insertbackground=TEXT)
        return e

    class ModernSelect(tk.Frame):
        # Своё выпадающее поле вместо стандартного Combobox
        def __init__(self, parent, values, variable, width=9, bg=None):
            super().__init__(parent, bg=bg or CARD)
            self._vals = list(values)
            self._var = variable
            self._bg = bg or CARD
            self._pop = None
            self._box = tk.Frame(self, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
            self._box.pack(fill="x")
            self._lb = tk.Label(self._box, textvariable=variable, font=("Segoe UI", 9),
                                bg=FIELD, fg=TEXT, width=width, anchor="w")
            self._lb.pack(side="left", padx=(6, 0), pady=4)
            self._arr = tk.Label(self._box, text="▾", font=("Segoe UI", 9), bg=FIELD, fg=ACCENT)
            self._arr.pack(side="right", padx=(0, 6))
            for ch in (self, self._box, self._lb, self._arr):
                ch.bind("<Button-1>", self._toggle)

        def _toggle(self, e=None):
            if self._pop:
                self._close()
            else:
                self._open()

        def _open(self):
            pop = tk.Toplevel(self)
            pop.wm_overrideredirect(True)
            pop.configure(bg=BORDER)
            try:
                pop.wm_geometry(f"+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height() + 2}")
                pop.minsize(self.winfo_width(), 0)
            except Exception:
                pass
            for v in self._vals:
                b = tk.Label(pop, text=f"  {v}", font=("Segoe UI", 9),
                             bg=CARD, fg=TEXT, anchor="w")
                b.pack(fill="x", padx=1, pady=0)
                b.bind("<Enter>", lambda ev, w=b: w.config(bg=FIELD))
                b.bind("<Leave>", lambda ev, w=b: w.config(bg=CARD))
                b.bind("<Button-1>", lambda ev, val=v: self._pick(val))
            self._pop = pop
            pop.bind("<Escape>", lambda e: self._close())
            pop.bind("<FocusOut>", lambda e: self._close())

        def _pick(self, v):
            self._var.set(v)
            self._close()

        def _close(self):
            try:
                if self._pop:
                    self._pop.destroy()
            except Exception:
                pass
            self._pop = None

        def set_values(self, values, keep_selection=True):
            cur = self._var.get()
            self._vals = list(values)
            if keep_selection and cur in self._vals:
                self._var.set(cur)
            else:
                self._var.set(self._vals[0] if self._vals else "")

    class DiskList(tk.Frame):
        # Свой список дисков вместо стандартного Listbox
        def __init__(self, parent):
            super().__init__(parent, bg=CARD)
            self._disks = []
            self._sel = None
            self._on_change = None

        def set_disks(self, disks, auto_select_single=True):
            for ch in list(self.winfo_children()):
                try:
                    ch.destroy()
                except Exception:
                    pass
            self._disks = list(disks)
            self._sel = None
            if not disks:
                tk.Label(self, text="Нет дисков — вставь флешку и нажми «Обновить»",
                         font=("Segoe UI", 9), bg=CARD, fg=MUTED).pack(anchor="w", pady=6)
                return
            for d in disks:
                row = tk.Frame(self, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
                row._disk_num = d["number"]
                row.pack(fill="x", pady=2)
                dot = tk.Canvas(row, width=16, height=16, highlightthickness=0, bd=0, bg=FIELD)
                dot.pack(side="left", padx=(8, 0), pady=6)
                dot.create_oval(3, 3, 13, 13,
                                fill=SUCCESS if d.get("is_usb") else MUTED, outline="")
                tag = "USB" if d.get("is_usb") else "ДИСК"
                txt = tk.Label(row, text=f"[Диск {d['number']}]  {tag}  {d['size']}  {d['model']}",
                               font=("Consolas", 10), bg=FIELD, fg=TEXT, anchor="w")
                txt.pack(side="left", fill="x", expand=True, padx=(4, 8), pady=6)
                row.bind("<Button-1>", lambda e, dd=d: self.select(dd["number"]))
                dot.bind("<Button-1>", lambda e, dd=d: self.select(dd["number"]))
                txt.bind("<Button-1>", lambda e, dd=d: self.select(dd["number"]))
            if auto_select_single and len(disks) == 1:
                self.select(disks[0]["number"])
            else:
                self._paint()

        def _paint(self):
            for row in self.winfo_children():
                try:
                    info = row._disk_num
                except Exception:
                    continue
                sel = (info == self._sel)
                bg = ACCENT if sel else FIELD
                fg = "#FFFFFF" if sel else TEXT
                try:
                    row.config(bg=bg, highlightbackground=ACCENT if sel else BORDER)
                    for ch in row.winfo_children():
                        if isinstance(ch, tk.Label):
                            ch.config(bg=bg, fg=fg)
                        elif isinstance(ch, tk.Canvas):
                            ch.config(bg=bg)
                except Exception:
                    pass

        def select(self, number):
            for row in self.winfo_children():
                try:
                    if row._disk_num == number:
                        self._sel = number
                        break
                except Exception:
                    continue
            else:
                self._sel = number
            self._paint()
            if self._on_change:
                try:
                    self._on_change()
                except Exception:
                    pass

        def get_selected(self):
            for d in self._disks:
                if d["number"] == self._sel:
                    return d
            return None

    def card(parent, **kw):
        f = tk.Frame(parent, bg=CARD, highlightthickness=1,
                     highlightbackground=BORDER, bd=0, **kw)
        return f

    main_frame = tk.Frame(root, bg=BG, padx=12, pady=10)
    main_frame.pack(fill="both", expand=True)

    header_card = card(main_frame)
    header_card.pack(fill="x", pady=(0, 10))
    header_top = tk.Frame(header_card, bg=CARD)
    header_top.pack(fill="x", padx=12, pady=(8, 8))
    tk.Label(header_top, text="USB Formatter", font=("Segoe UI", 14, "bold"),
             bg=CARD, fg=TEXT).pack(side="left")
    theme_box = tk.Frame(header_top, bg=CARD)
    theme_box.pack(side="right")
    tk.Label(theme_box, text="Тема:", font=("Segoe UI", 9), bg=CARD, fg=MUTED).pack(side="left", padx=(0, 6))
    theme_light_btn = RoundedButton(theme_box, text="Светлая", command=None,
                                    width=88, height=30, radius=10,
                                    font=("Segoe UI", 9, "bold"),
                                    secondary=(_theme_name != "light"))
    theme_light_btn.pack(side="left", padx=(0, 4))
    theme_dark_btn = RoundedButton(theme_box, text="Тёмная", command=None,
                                   width=88, height=30, radius=10,
                                   font=("Segoe UI", 9, "bold"),
                                   secondary=(_theme_name != "dark"))
    theme_dark_btn.pack(side="left")

    def _pick_theme(name):
        if name == _theme_name:
            return
        if save_theme_name(name):
            # Мгновенно: закрываем окно, сессия пересоберётся в том же процессе
            _rebuild["flag"] = True
            try:
                root.destroy()
            except Exception:
                pass
        else:
            messagebox.showerror("Тема", "Не удалось сохранить тему.")

    theme_light_btn.set_command(lambda: _pick_theme("light"))
    theme_dark_btn.set_command(lambda: _pick_theme("dark"))

    # Прав администратора всегда хватает (ensure_admin при старте),
    # кнопка перезапуска убрана. Заглушка оставлена для вызовов ниже.
    def refresh_admin_label():
        return True

    # --- Список дисков ---
    list_card = card(main_frame)
    list_card.pack(fill="x", pady=(0, 8))
    tk.Label(list_card, text="ШАГ 1 — ВЫБЕРИ ФЛЕШКУ", font=("Segoe UI", 9, "bold"),
             bg=CARD, fg=MUTED).pack(anchor="w", padx=12, pady=(10, 4))

    show_all_var = tk.BooleanVar(value=False)

    disk_list = DiskList(list_card)
    disk_list.pack(fill="x", padx=12, pady=(0, 4))

    opts_frame = tk.Frame(list_card, bg=CARD)
    opts_frame.pack(fill="x", padx=12, pady=(8, 10))

    show_all_cb = ModernCheck(opts_frame, text="Показать все диски (опасно)",
                              variable=show_all_var, command=lambda: refresh_disks())
    show_all_cb.pack(side="left")

    refresh_btn = RoundedButton(opts_frame, text="Обновить",
                                command=lambda: refresh_disks(),
                                width=130, height=32, radius=10,
                                font=("Segoe UI", 9, "bold"), secondary=True)
    refresh_btn.pack(side="right")

    # --- Параметры ---
    params_card = card(main_frame)
    params_card.pack(fill="x", pady=(0, 8))
    tk.Label(params_card, text="ШАГ 2 — ПАРАМЕТРЫ", font=("Segoe UI", 9, "bold"),
             bg=CARD, fg=MUTED).pack(anchor="w", padx=12, pady=(10, 4))

    fs_var = tk.StringVar(value="NTFS")
    fs_row = tk.Frame(params_card, bg=CARD)
    fs_row.pack(fill="x", padx=12)
    tk.Label(fs_row, text="Система:", font=("Segoe UI", 9), bg=CARD, fg=TEXT).pack(side="left")
    for _txt, _val in (("exFAT", "EXFAT"), ("NTFS", "NTFS"), ("FAT32 (до 32 ГБ)", "FAT32")):
        ModernRadio(fs_row, text=_txt, value=_val, variable=fs_var).pack(side="left", padx=(8, 0))

    label_row = tk.Frame(params_card, bg=CARD)
    label_row.pack(fill="x", padx=12, pady=(8, 0))
    tk.Label(label_row, text="Имя:", font=("Segoe UI", 9), bg=CARD, fg=TEXT).pack(side="left")
    label_var = tk.StringVar(value="USB")
    label_entry = modern_entry(label_row, label_var, width=20)
    label_entry.pack(side="left", padx=(8, 0))

    quick_var = tk.BooleanVar(value=True)
    ModernCheck(params_card,
                text="Быстрое форматирование (убери галочку только если флешка с ошибками)",
                variable=quick_var).pack(anchor="w", padx=12, pady=(8, 10))

    # --- Кнопка Дополнительно ВЫШЕ кнопки форматирования (п.3) ---
    adv_top_frame = tk.Frame(main_frame, bg=BG)
    adv_top_frame.pack(fill="x", pady=(0, 8))
    adv_open_btn = RoundedButton(adv_top_frame, text="Дополнительно: тома, буквы, MBR/GPT…",
                                 command=None, width=632, height=34, radius=12,
                                 font=("Segoe UI", 10, "bold"), secondary=True)
    adv_open_btn.pack(fill="x")

    # --- Главная кнопка ---
    format_holder = tk.Frame(main_frame, bg=BG)
    format_holder.pack(fill="x")
    format_btn = RoundedButton(format_holder, text="ОТФОРМАТИРОВАТЬ",
                               command=None, width=632, height=44, radius=14,
                               font=("Segoe UI", 12, "bold"))
    format_btn.pack(fill="x")

    # --- Прогресс ---
    progress_card = card(main_frame)
    progress_card.pack(fill="x", pady=(0, 8))
    tk.Label(progress_card, text="ПРОГРЕСС", font=("Segoe UI", 9, "bold"),
             bg=CARD, fg=MUTED).pack(anchor="w", padx=12, pady=(10, 4))
    progress_frame = tk.Frame(progress_card, bg=CARD)
    progress_frame.pack(fill="x", padx=12)
    progress_bar = ModernProgress(progress_frame, width=430, height=14)
    progress_bar.pack(fill="x", expand=True)
    # Отдельной строкой на всю ширину — ничего не обрезается
    progress_label = tk.Label(progress_card, text="Готов", font=("Segoe UI", 9),
                              bg=CARD, fg=MUTED, anchor="w", justify="left",
                              wraplength=600)
    progress_label.pack(fill="x", padx=12, pady=(4, 0))
    tk.Frame(progress_card, bg=CARD, height=10).pack()

    # Состояние операции для прогресса/времени
    op_state = {"start": 0.0, "active": False}

    def set_progress(pct, text=None):
        try:
            pct = max(0, min(100, int(pct)))
        except Exception:
            pct = 0
        try:
            progress_bar.set(pct)
        except Exception:
            pass
        if text is None:
            elapsed = time.time() - op_state.get("start", time.time()) if op_state.get("active") else 0
            if pct > 3 and elapsed > 1 and pct < 100:
                total_est = elapsed / (pct / 100.0)
                left = max(0, total_est - elapsed)
                text = f"{pct}% · прошло {elapsed:.0f}с · осталось ~{left:.0f}с"
            elif pct >= 100:
                text = "Готово"
            else:
                text = f"{pct}%"
        try:
            progress_label.config(text=text)
        except Exception:
            pass
        try:
            root.update_idletasks()
        except Exception:
            pass

    # --- Лог ---
    log_card = card(main_frame)
    log_card.pack(fill="both", expand=True)
    tk.Label(log_card, text="СТАТУС", font=("Segoe UI", 9, "bold"),
             bg=CARD, fg=MUTED).pack(anchor="w", padx=12, pady=(10, 4))
    log_text = tk.Text(log_card, height=7, font=("Consolas", 9), wrap="word",
                       bg=FIELD, fg=TEXT, relief="flat",
                       highlightthickness=1, highlightbackground=BORDER,
                       insertbackground=TEXT)
    log_text.pack(fill="both", expand=True, padx=12, pady=(0, 10))
    log_text.config(state="disabled")

    def log(msg):
        log_text.config(state="normal")
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.config(state="disabled")
        root.update_idletasks()

    def _apply_shown(shown):
        try:
            disk_list.set_disks(shown)
        except Exception as e:
            log(f"Ошибка списка: {e}")
            return
        if len(shown) == 0:
            log("USB-флешки не найдены. Вставь флешку и нажми Обновить.")
            if not show_all_var.get():
                log("Подсказка: включи 'Показать все диски', если это точно флешка.")
        else:
            log(f"Найдено: {len(shown)}. Выбери строку и жми ОТФОРМАТИРОВАТЬ.")

    def _filter_disks(disks):
        shown = []
        for d in disks or []:
            # Системный диск вообще не показываем
            if d.get("is_system"):
                continue
            # Обычный режим — только USB для простоты
            if not show_all_var.get() and not d.get("is_usb"):
                continue
            shown.append(d)
        return shown

    def refresh_disks():
        # Ничего тяжёлого в UI-потоке: кэш сразу, свежее — фоном
        if not is_admin():
            log("Нет прав администратора — список дисков будет пуст.")
            refresh_admin_label()
            try:
                disk_list.set_disks([])
            except Exception:
                pass
            return

        refresh_admin_label()

        _disk_cache["seq"] += 1
        my_seq = _disk_cache["seq"]

        cached = _disk_cache["disks"]
        if cached is not None:
            _apply_shown(_filter_disks(cached))
            log("Обновляю список дисков…")
        else:
            log("Читаю список дисков…")

        # Индикатор на полосе, чтобы было видно что идёт опрос
        try:
            if not op_state.get("active"):
                set_progress(12, "Чтение дисков…")
        except Exception:
            pass

        def _bg():
            try:
                disks = get_disks()
            except Exception as e:
                disks = e
            try:
                root.after(0, lambda: _on_loaded(my_seq, disks))
            except Exception:
                pass

        def _on_loaded(seq, result):
            try:
                alive = root.winfo_exists()
            except Exception:
                alive = False
            if not alive:
                return
            if seq != _disk_cache["seq"]:
                return  # устаревший ответ, уже есть новее
            if isinstance(result, Exception):
                log(f"Ошибка чтения дисков: {result}")
                try:
                    if not op_state.get("active"):
                        set_progress(0, "Ошибка чтения")
                except Exception:
                    pass
                return
            _disk_cache["disks"] = result
            _apply_shown(_filter_disks(result))
            try:
                t = dict(_last_load_times)
                if t:
                    log(f"⏱ опрос: diskpart {t.get('diskpart', 0):.1f}с, "
                        f"WMI {t.get('hw', 0):.1f}с, всего {t.get('total', 0):.1f}с")
                if not op_state.get("active"):
                    set_progress(0, "Готов")
            except Exception:
                pass

        threading.Thread(target=_bg, daemon=True).start()

    def get_selected_disk():
        try:
            return disk_list.get_selected()
        except Exception:
            return None

    def do_format_thread(disk, filesystem, label, quick):
        def _cb(pct, stage_text):
            # callback из рабочего потока — перекидываем в UI-поток
            def _ui():
                elapsed = time.time() - op_state.get("start", time.time())
                if pct < 100 and elapsed > 1:
                    total_est = elapsed / (max(1, pct) / 100.0)
                    left = max(0, total_est - elapsed)
                    set_progress(pct, f"{pct}% · {stage_text} · {elapsed:.0f}с / ост ~{left:.0f}с")
                else:
                    set_progress(pct, f"{pct}% · {stage_text}")
            try:
                root.after(0, _ui)
            except Exception:
                pass

        try:
            success, result, elapsed = format_disk(
                disk["number"],
                filesystem,
                label,
                quick,
                size_gb=disk.get("size_gb"),
                letter=None,
                progress_callback=_cb
            )
            if success:
                root.after(0, lambda: on_format_done(
                    True,
                    f"ГОТОВО за {elapsed:.1f} сек.\n"
                    f"Буква: {result['letter']}:  "
                    f"{result['filesystem']}  {result['label']}"
                ))
            else:
                root.after(0, lambda: on_format_done(False, str(result)))
        except Exception as e:
            root.after(0, lambda ex=e: on_format_done(False, f"Ошибка: {ex}"))

    def on_format_done(success, msg):
        op_state["active"] = False
        try:
            format_btn.set_enabled(True)
            format_btn.set_text("ОТФОРМАТИРОВАТЬ")
        except Exception:
            pass
        if success:
            set_progress(100, "Готово")
            log(msg)
            messagebox.showinfo("Готово", msg)
        else:
            set_progress(0, "Ошибка")
            log("ОШИБКА: " + msg)
            messagebox.showerror("Ошибка", msg)
        refresh_disks()

    def on_format():
        disk = get_selected_disk()
        if disk is None:
            messagebox.showwarning(
                "Выбери диск",
                "Сначала выбери флешку из списка."
            )
            return

        filesystem = fs_var.get().upper()
        raw_label = label_var.get().strip() or DEFAULT_LABEL
        clean_label = re.sub(r'[^A-Za-zА-Яа-я0-9 _.-]', "", raw_label)[:32] or DEFAULT_LABEL
        quick = quick_var.get()

        # Проверка FAT32 заранее — чтобы не ждать
        if filesystem == "FAT32" and (disk.get("size_gb") or 0) > 32:
            messagebox.showerror(
                "FAT32 не подойдёт",
                f"Диск {disk['size']}, а FAT32 работает только до 32 ГБ.\n"
                "Выбери exFAT или NTFS."
            )
            return

        # Подтверждение 1
        warn_extra = ""
        if not disk.get("is_usb"):
            warn_extra = "\n\n⚠ ЭТО НЕ USB! Ты включил показ всех дисков."

        ok1 = messagebox.askyesno(
            "Внимание — данные будут удалены",
            f"Диск {disk['number']}  {disk['model']}  {disk['size']}{warn_extra}\n"
            f"Система: {filesystem}  Имя: {clean_label}\n\n"
            "ВСЁ на этом диске будет стёрто. Продолжить?"
        )
        if not ok1:
            return

        # Подтверждение 2 — финальное
        ok2 = messagebox.askyesno(
            "Последнее предупреждение",
            f"Точно форматировать Диск {disk['number']}?\n"
            "Это последнее окно перед удалением."
        )
        if not ok2:
            return

        op_state["start"] = time.time()
        op_state["active"] = True
        set_progress(3, "Старт... не вынимай флешку")
        try:
            format_btn.set_enabled(False)
            format_btn.set_text("Работаю... не вынимай флешку")
        except Exception:
            pass
        log(
            f"Форматирую Диск {disk['number']} как {filesystem} "
            f"({'быстро' if quick else 'полно'})..."
        )
        t = threading.Thread(
            target=do_format_thread,
            args=(disk, filesystem, clean_label, quick),
            daemon=True
        )
        t.start()

    format_btn.set_command(on_format)

    # ========================================================
    # ОКНО ДОПОЛНИТЕЛЬНО — тома, буквы, MBR/GPT, clean all
    # ========================================================

    def open_advanced():
        disk = get_selected_disk()
        if disk is None:
            messagebox.showwarning("Дополнительно", "Сначала выбери флешку в главном окне.")
            return

        adv = tk.Toplevel(root)
        adv.title(f"Дополнительно — Диск {disk['number']}")
        # Высота под экран: компактно, низ не уходит за границы
        try:
            _ash = root.winfo_screenheight()
        except Exception:
            _ash = 800
        _adv_h = min(560, _ash - 80)
        adv.geometry(f"620x{_adv_h}")
        adv.minsize(600, 480)
        adv.resizable(False, True)
        adv.configure(bg=BG)
        adv.transient(root)
        adv.grab_set()
        try:
            _ax = root.winfo_rootx() + 20
            _ay = max(0, root.winfo_rooty() + 10)
            adv.geometry(f"620x{_adv_h}+{_ax}+{_ay}")
        except Exception:
            pass
        try:
            adv.attributes("-alpha", 0.0)
        except Exception:
            pass
        _fade_in(adv)

        head = card(adv)
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(head, text=f"Диск {disk['number']}  {disk['model']}  {disk['size']}",
                 font=("Segoe UI", 10, "bold"), bg=CARD, fg=TEXT,
                 wraplength=560, justify="left").pack(anchor="w", padx=12, pady=(8, 0))
        # Данные подгрузятся фоном — окно открывается мгновенно
        cur_var = tk.StringVar(value="Сейчас: чтение разделов…")
        tk.Label(head, textvariable=cur_var, font=("Segoe UI", 9), bg=CARD, fg=MUTED,
                 wraplength=560, justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        opts = card(adv)
        opts.pack(fill="x", padx=12, pady=(0, 8))
        row_top = tk.Frame(opts, bg=CARD)
        row_top.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(row_top, text="Стиль:", font=("Segoe UI", 9), bg=CARD, fg=TEXT).pack(side="left")
        style_var = tk.StringVar(value="KEEP")
        for _t, _v in (("Как есть", "KEEP"), ("MBR", "MBR"), ("GPT", "GPT")):
            ModernRadio(row_top, text=_t, value=_v, variable=style_var).pack(side="left", padx=(8, 0))
        clean_all_var = tk.BooleanVar(value=False)
        ModernCheck(opts, text="Полная очистка clean all (долго, зануляет)",
                    variable=clean_all_var).pack(anchor="w", padx=12, pady=(6, 10))

        vols = card(adv)
        vols.pack(fill="x", padx=12, pady=(0, 8))
        top_row = tk.Frame(vols, bg=CARD)
        top_row.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top_row, text="ТОМА", font=("Segoe UI", 9, "bold"), bg=CARD, fg=MUTED).pack(side="left")

        # Стартовые буквы без ожидания powershell — точный список подтянется фоном
        fs_choices = ["NTFS", "EXFAT", "FAT32"]
        letter_choices = ["Авто"] + [chr(c) for c in range(ord("D"), ord("Z") + 1)]

        hdr = tk.Frame(vols, bg=CARD)
        hdr.pack(fill="x", padx=12)
        for _txt, _w in (("Том", 6), ("Название", 12), ("Формат", 9), ("Буква", 7), ("Размер, МБ", 11), ("", 4)):
            tk.Label(hdr, text=_txt, font=("Segoe UI", 8, "bold"), bg=CARD, fg=MUTED, width=_w).pack(side="left", padx=1)

        rows_frame = tk.Frame(vols, bg=CARD)
        rows_frame.pack(fill="x", padx=12, pady=(4, 0))
        rows = []

        def add_row():
            if len(rows) >= 4:
                messagebox.showinfo("Дополнительно", "Максимум 4 тома.")
                return
            i = len(rows)
            fr = tk.Frame(rows_frame, bg=CARD)
            fr.pack(fill="x", pady=2)
            tk.Label(fr, text=f"Том {i + 1}", font=("Segoe UI", 9, "bold"),
                     bg=CARD, fg=TEXT, width=6).pack(side="left", padx=1)
            lv = tk.StringVar(value=f"USB{i + 1}")
            modern_entry(fr, lv, width=12).pack(side="left", padx=1)
            fv = tk.StringVar(value="NTFS" if i == 0 else "EXFAT")
            ModernSelect(fr, fs_choices, fv, width=7).pack(side="left", padx=1)
            lt_v = tk.StringVar(value="Авто")
            lt_sel = ModernSelect(fr, letter_choices, lt_v, width=5)
            lt_sel.pack(side="left", padx=1)
            sv = tk.StringVar(value="")
            modern_entry(fr, sv, width=11).pack(side="left", padx=1)
            rm = RoundedButton(fr, text="−", command=None, width=32, height=28,
                               radius=9, font=("Segoe UI", 12, "bold"), secondary=True)
            rm.pack(side="left", padx=1)
            entry = {"frame": fr, "label": lv, "fs": fv, "letter": lt_v,
                     "letter_sel": lt_sel, "size": sv}
            rm.set_command(lambda e=entry: remove_row(e))
            rows.append(entry)
            _renumber()

        def remove_row(entry):
            if len(rows) <= 1:
                messagebox.showinfo("Дополнительно", "Должен остаться хотя бы один том.")
                return
            try:
                rows.remove(entry)
                entry["frame"].destroy()
            except Exception:
                pass
            _renumber()

        def _renumber():
            for idx, r in enumerate(rows):
                for child in r["frame"].winfo_children():
                    if isinstance(child, tk.Label) and child.cget("text").startswith("Том"):
                        child.config(text=f"Том {idx + 1}")
                        break

        btn_row = tk.Frame(vols, bg=CARD)
        btn_row.pack(fill="x", padx=12, pady=(6, 10))
        plus_btn = RoundedButton(btn_row, text="+ Добавить том", command=add_row,
                                 width=170, height=32, radius=10,
                                 font=("Segoe UI", 9, "bold"), secondary=True)
        plus_btn.pack(side="left")
        tk.Label(btn_row, text="пустой размер = остаток (только последний том)",
                 font=("Segoe UI", 8), bg=CARD, fg=MUTED).pack(side="left", padx=(8, 0))

        add_row()

        # Фоновая подгрузка разделов и свободных букв — окно уже открыто
        def _adv_bg_load():
            try:
                parts = get_partitions(disk["number"])
            except Exception:
                parts = []
            try:
                free = get_free_letters()
            except Exception:
                free = [chr(c) for c in range(ord("D"), ord("Z") + 1)]

            def _apply():
                try:
                    if parts:
                        cur_var.set("Сейчас: " + "; ".join(
                            f"том {p['partition']} {p['size_gb']} ГБ буква {p['letter'] or '-'}"
                            for p in parts))
                    else:
                        cur_var.set("Сейчас: разделы не прочитаны или их нет.")
                except Exception:
                    pass
                try:
                    vals = ["Авто"] + free
                    for r in rows:
                        try:
                            r["letter_sel"].set_values(vals, keep_selection=True)
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                adv.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_adv_bg_load, daemon=True).start()

        adv_log = tk.Text(adv, height=4, font=("Consolas", 9), wrap="word",
                          bg=FIELD, fg=TEXT, relief="flat",
                          highlightthickness=1, highlightbackground=BORDER,
                          insertbackground=TEXT)
        adv_log.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        adv_log.config(state="disabled")

        def adv_log_msg(m):
            adv_log.config(state="normal")
            adv_log.insert("end", m + "\n")
            adv_log.see("end")
            adv_log.config(state="disabled")

        def on_adv_apply():
            parts = []
            for i, r in enumerate(rows):
                raw_size = r["size"].get().strip()
                size_mb = None
                if raw_size != "":
                    try:
                        size_mb = int(raw_size)
                    except ValueError:
                        messagebox.showerror("Дополнительно", f"Том {i + 1}: размер — число в МБ или пусто.")
                        return
                lt = r["letter"].get()
                parts.append({
                    "size_mb": size_mb,
                    "fs": r["fs"].get(),
                    "label": r["label"].get().strip() or f"USB{i + 1}",
                    "letter": None if lt == "Авто" else lt
                })
            style = None if style_var.get() == "KEEP" else style_var.get()
            desc = ", ".join(f"{p['fs']} {p['size_mb'] or 'остаток'}МБ буква {p['letter'] or 'авто'}" for p in parts)
            if not messagebox.askyesno("Точно разбить?",
                                       f"Диск {disk['number']} будет ПОЛНОСТЬЮ стёрт.\n"
                                       f"Стиль: {style or 'как есть'}, очистка: {'clean all' if clean_all_var.get() else 'clean'}.\n"
                                       f"Тома: {desc}\n\nПродолжить?"):
                return
            if not messagebox.askyesno("Последнее окно", "Это необратимо. Начать?"):
                return
            try:
                apply_btn.set_enabled(False)
                apply_btn.set_text("Работаю...")
            except Exception:
                pass
            adv_log_msg("Старт... не вынимай флешку.")

            def _worker():
                def _cb(pct, txt):
                    try:
                        root.after(0, lambda: set_progress(pct, f"Доп: {txt}"))
                    except Exception:
                        pass
                    try:
                        root.after(0, lambda t=f"{pct}% {txt}": adv_log_msg(t))
                    except Exception:
                        pass
                op_state["start"] = time.time()
                op_state["active"] = True
                try:
                    ok, res, el = format_disk_advanced(disk["number"], parts, style=style,
                                                      clean_all=clean_all_var.get(),
                                                      progress_callback=_cb)
                    def _done():
                        op_state["active"] = False
                        try:
                            apply_btn.set_enabled(True)
                            apply_btn.set_text("Применить: стереть и разбить")
                        except Exception:
                            pass
                        if ok:
                            set_progress(100, "Готово")
                            adv_log_msg(f"ГОТОВО за {el:.1f} сек.")
                            messagebox.showinfo("Готово", f"Разбивка выполнена за {el:.1f} сек.")
                            refresh_disks()
                            adv.destroy()
                        else:
                            set_progress(0, "Ошибка")
                            adv_log_msg("ОШИБКА: " + str(res)[:2000])
                            messagebox.showerror("Ошибка", str(res)[:2000])
                    root.after(0, _done)
                except Exception as e:
                    def _err():
                        try:
                            apply_btn.set_enabled(True)
                        except Exception:
                            pass
                        adv_log_msg(f"Ошибка: {e}")
                    root.after(0, _err)
            threading.Thread(target=_worker, daemon=True).start()

        btn_holder = tk.Frame(adv, bg=BG)
        btn_holder.pack(fill="x", padx=12, pady=(0, 12))
        apply_btn = RoundedButton(btn_holder, text="Применить: стереть и разбить",
                                  command=on_adv_apply, width=616, height=42,
                                  radius=13, font=("Segoe UI", 10, "bold"))
        apply_btn.pack(fill="x")

    adv_open_btn.set_command(open_advanced)

    refresh_admin_label()
    # Окно показываем сразу, список дисков подгружается следом —
    # без зависания на чёрном месте перед появлением
    try:
        log("Открываю окно… список дисков подгружается.")
    except Exception:
        pass
    try:
        root.after(80, refresh_disks)
    except Exception:
        refresh_disks()
    _fade_in(root)

    # Страховка: через 0.6с окно обязано быть непрозрачным,
    # даже если анимация по какой-то причине замерла
    def _force_opaque():
        try:
            root.attributes("-alpha", 1.0)
        except Exception:
            pass
    try:
        root.after(600, _force_opaque)
    except Exception:
        pass
    root.mainloop()
    return _rebuild["flag"]


def gui_main():
    # Цикл сессий: переключение темы пересобирает окно за ~0.3с
    # вместо перезапуска всего процесса
    while True:
        try:
            again = _gui_session()
        except Exception:
            raise
        if not again:
            break


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Всегда с правами администратора: без них — UAC и перезапуск
    ensure_admin()

    # --console для старого текстового режима, по умолчанию GUI
    if "--console" in sys.argv:
        try:
            main()
        except KeyboardInterrupt:
            print("\n\nПрограмма остановлена.")
        except Exception as error:
            print()
            print("КРИТИЧЕСКАЯ ОШИБКА:")
            print(error)
            input("\nНажмите Enter...")
    else:
        try:
            gui_main()
        except Exception as error:
            # В exe без консоли print не виден — показываем окном
            try:
                import tkinter.messagebox as _mb
                _mb.showerror("USB Formatter", f"Не удалось запустить GUI:\n{error}")
            except Exception:
                pass
            print(f"Не удалось запустить GUI ({error}), запускаю консоль...")
            try:
                main()
            except KeyboardInterrupt:
                print("\n\nПрограмма остановлена.")