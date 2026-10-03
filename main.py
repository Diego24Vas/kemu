#!/usr/bin/env python3
import json
import subprocess
import os
import sys
import argparse
import shutil
import re
import time
import hashlib

try:
    import questionary
    from questionary import Choice, Separator
    from questionary.question import Question
except ModuleNotFoundError:
    print("\n[Error Crítico] No se encontró el módulo 'questionary'.")
    print("[*] Asegúrate de activar el entorno virtual o instalar las dependencias:")
    print("    source .venv/bin/activate")
    print("    pip install -r requirements.txt\n")
    sys.exit(1)

_original_ask = Question.ask
def _safe_ask(self, patch_stdout=False, *args, **kwargs):
    try:
        result = self.unsafe_ask(patch_stdout=patch_stdout)
    except KeyboardInterrupt:
        result = None
    if result is None:
        print("\n[!] Asistente cancelado.")
        sys.exit(0)
    return result
Question.ask = _safe_ask

KEMU_DIR = os.path.expanduser("~/kemu")
_LEGACY_DIR = os.path.expanduser("~/qemu")
if not os.path.exists(KEMU_DIR) and os.path.isdir(_LEGACY_DIR):
    try:
        shutil.move(_LEGACY_DIR, KEMU_DIR)
    except Exception:
        pass
QEMU_DIR = KEMU_DIR


def formatear_memoria_kb(kb):
    gb = kb / (1024 * 1024)
    if gb >= 1.0:
        return f"{gb:.1f}G"
    mb = kb / 1024
    return f"{mb:.0f}M"


def formatear_espacio_bytes(bytes_cant):
    gb = bytes_cant / (1024**3)
    if gb >= 1.0:
        return f"{gb:.1f}G"
    mb = bytes_cant / (1024**2)
    return f"{mb:.0f}M"


def formatear_tamano(bytes_tam):
    if bytes_tam >= 1024**3:
        return f"{bytes_tam / (1024**3):.1f}G"
    elif bytes_tam >= 1024**2:
        return f"{bytes_tam / (1024**2):.1f}M"
    elif bytes_tam >= 1024:
        return f"{bytes_tam // 1024}K"
    else:
        return f"{bytes_tam}B"


def generar_mac(nombre_vm):
    """Genera una dirección MAC determinista en el rango privado OUI de QEMU (52:54:00) para evitar colisiones."""
    h = hashlib.md5(nombre_vm.encode("utf-8")).hexdigest()
    return f"52:54:00:{h[0:2]}:{h[2:4]}:{h[4:6]}"


def info_sistema():
    info = {
        "ram_total": "Desconocido",
        "ram_disp": "Desconocido",
        "cpu_log": os.cpu_count() or 0,
        "disco_disp": "Desconocido"
    }

    try:
        mem_free = 0
        buffers = 0
        cached = 0
        has_available = False
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    k, v = parts[0], int(parts[1])
                    if k == "MemTotal:":
                        info["ram_total"] = formatear_memoria_kb(v)
                    elif k == "MemAvailable:":
                        info["ram_disp"] = formatear_memoria_kb(v)
                        has_available = True
                    elif k == "MemFree:":
                        mem_free = v
                    elif k == "Buffers:":
                        buffers = v
                    elif k == "Cached:":
                        cached = v
        if not has_available and mem_free > 0:
            info["ram_disp"] = formatear_memoria_kb(mem_free + buffers + cached)
    except Exception:
        pass

    target_dir = QEMU_DIR if os.path.isdir(QEMU_DIR) else os.path.expanduser("~")
    try:
        st = os.statvfs(target_dir)
        disp_bytes = st.f_frsize * st.f_bavail
        info["disco_disp"] = formatear_espacio_bytes(disp_bytes)
    except Exception:
        pass

    return info


def guardar_config(vm_dir, cfg):
    data = {
        "ram": cfg["ram"],
        "cpu": cfg["cpu"],
        "tipo_conexion": cfg["tipo_conexion"],
        "modelo_adaptador": cfg.get("modelo_adaptador", ""),
        "nombre_bridge": cfg.get("nombre_bridge", ""),
    }
    path = os.path.join(vm_dir, f"{cfg['nombre']}.json")
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"\n[Error] No se pudo guardar la configuración en '{path}': {e}")


def cargar_config(vm_dir, nombre):
    path = os.path.join(vm_dir, f"{nombre}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"\n[Advertencia] No se pudo leer el archivo de configuración '{path}': {e}")
        return None


def detectar_bridges():
    bridges = []
    net_dir = "/sys/class/net"
    if os.path.isdir(net_dir):
        for iface in sorted(os.listdir(net_dir)):
            if os.path.exists(os.path.join(net_dir, iface, "bridge")):
                bridges.append(iface)
    return bridges


def verificar_permiso_bridge(bridge_name):
    conf_path = "/etc/qemu/bridge.conf"
    if not os.path.isfile(conf_path):
        return False, f"No existe el archivo {conf_path}."
    try:
        with open(conf_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "allow":
                    if parts[1] == "all" or parts[1] == bridge_name:
                        return True, ""
        return False, f"La interfaz '{bridge_name}' no está autorizada en {conf_path}."
    except Exception as e:
        return False, f"Error al leer {conf_path}: {e}"


def validar_ruta_iso(p):
    if not p:
        return "Debes ingresar una ruta."
    ruta = os.path.expanduser(p.strip())
    if not os.path.exists(ruta):
        return f"No se encontró el archivo: {ruta}"
    if not os.path.isfile(ruta):
        return f"La ruta debe ser un archivo, no un directorio: {ruta}"
    if not ruta.lower().endswith(".iso"):
        return "El archivo debe tener extensión .iso"
    return True


def validar_ruta_disco(p):
    if not p:
        return "Debes ingresar una ruta."
    ruta = os.path.expanduser(p.strip())
    if not os.path.exists(ruta):
        return f"No se encontró el archivo: {ruta}"
    if not os.path.isfile(ruta):
        return f"La ruta debe ser un archivo, no un directorio: {ruta}"
    if not ruta.lower().endswith(".qcow2"):
        return "El archivo debe tener extensión .qcow2"
    return True


def validar_tamano_disco(val):
    v = val.strip()
    if not v:
        return True
    match = re.match(r'^(\d+(\.\d+)?)([KMGTP]?)$', v, re.IGNORECASE)
    if match:
        num = float(match.group(1))
        unit = match.group(3)
        if not unit:
            return "Especifica la unidad de medida (ej: 20G, 50G, 1024M)."
        if num <= 0:
            return "El tamaño del disco debe ser mayor a 0."
        return True
    return "Formato inválido. Usa números seguidos de unidad (ej: 20G, 50G, 1024M)."


def validar_ram(val):
    v = val.strip()
    if not v:
        return "Debes especificar la cantidad de RAM."
    match = re.match(r'^(\d+)([MGTK]?)$', v, re.IGNORECASE)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if not unit:
            return "Especifica la unidad de RAM (ej: 2G, 4096M)."
        if num <= 0:
            return "La cantidad de RAM debe ser mayor a 0 (ej: 2G, 4096M)."
        return True
    return "Formato inválido para RAM (ej: 2G, 4G, 4096M)."


def validar_cpu(val):
    v = val.strip()
    if not v.isdigit() or int(v) <= 0:
        return "El número de núcleos debe ser un entero positivo (ej: 2, 4, 8)."
    return True


def manejar_disco_existente(disco_path, permitir_cambiar_nombre=True):
    if not os.path.exists(disco_path):
        return "new"

    choices = [
        Choice("Reutilizar (usar el disco tal cual)", value="reuse"),
        Choice("Sobrescribir (borrar y crear uno nuevo)", value="overwrite"),
    ]
    if permitir_cambiar_nombre:
        choices.append(Choice("Elegir otro nombre para la VM", value="rename"))
    choices.append(Choice("Cancelar operación", value="cancel"))

    return questionary.select(
        f"\n[!] El disco virtual ya existe en destino:\n    '{disco_path}'\n    ¿Qué deseas hacer?",
        choices=choices
    ).ask()


def pedir_config(nombre_sugerido=None, disco_origen=None):
    sis = info_sistema()

    default_name = (nombre_sugerido or "mi-vm").strip()
    default_name = re.sub(r'[/\\:\*\?"<>\|\s,]+', '-', default_name)
    default_name = re.sub(r'\.{2,}', '-', default_name)
    default_name = default_name.strip(".-") or "mi-vm"

    def validar_nombre_vm(texto):
        val = texto.strip()
        if not val:
            return "El nombre de la VM no puede estar vacío."
        if any(c in val for c in ('/', '\\', '..', ':', '*', '?', '"', '<', '>', '|', ',', ' ')):
            return "El nombre no puede contener espacios, comas ni caracteres especiales (/ \\ .. : * ? \" < > | , espacio)."
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', val):
            return "El nombre solo debe contener caracteres alfanuméricos, guiones (-) y puntos (.)."
        if val.startswith(".") or val.endswith("."):
            return "El nombre no puede empezar ni terminar con un punto."
        if val.lower().endswith(".qcow2"):
            return "No incluyas la extensión .qcow2 en el nombre de la VM."
        return True

    # 1. Identificar SO y Nombre primero para comprobar colisión de disco tempranamente
    tipo_os = questionary.select(
        "Selecciona el tipo de Sistema Operativo:",
        choices=[
            "Linux   (Drivers VirtIO nativos - Máximo rendimiento)",
            "Windows (Compatibilidad SATA/e1000 y optimizaciones Hyper-V)"
        ]
    ).ask()

    os_folder = "windows" if "Windows" in tipo_os else "linux"
    vm_dir = os.path.join(QEMU_DIR, os_folder)
    os.makedirs(vm_dir, exist_ok=True)

    # Comprobación de disco: si ya existe, se advierte antes de preguntar RAM, CPU o Red
    while True:
        nombre = questionary.text(
            "Nombre de la VM:",
            default=default_name,
            validate=validar_nombre_vm
        ).ask().strip()

        disco_path = os.path.join(vm_dir, f"{nombre}.qcow2")

        # Comprobar si es el mismo archivo que el origen (caso cmd_add)
        es_mismo_archivo = False
        if disco_origen and os.path.exists(disco_path):
            try:
                es_mismo_archivo = os.path.samefile(disco_origen, disco_path)
            except OSError:
                es_mismo_archivo = (os.path.abspath(disco_origen) == os.path.abspath(disco_path))

        if es_mismo_archivo:
            print(f"\n[*] El disco ya se encuentra en la ubicación de destino: {disco_path}")
            print("[+] Se reutilizará el archivo existente.")
            estado_disco = "samefile"
            break

        if os.path.exists(disco_path):
            estado_disco = manejar_disco_existente(disco_path, permitir_cambiar_nombre=True)
            if estado_disco == "cancel":
                print("\n[!] Operación cancelada.")
                sys.exit(0)
            elif estado_disco == "rename":
                default_name = f"{nombre}-nuevo"
                continue
            else:
                break
        else:
            estado_disco = "new"
            break

    # Si se reutiliza un disco existente, pre-cargar valores previos si existen
    config_previa = cargar_config(vm_dir, nombre) if estado_disco in ("reuse", "samefile") else None
    default_ram = config_previa.get("ram", "2G") if config_previa else "2G"
    default_cpu = str(config_previa.get("cpu", "2")) if config_previa else "2"
    default_net = config_previa.get("tipo_conexion") if config_previa else None

    # 2. Configuración de Hardware
    ram = questionary.text(
        f"Cantidad de RAM (ej: 2G, 4096M) [Disp: {sis['ram_disp']} / Total: {sis['ram_total']}]:",
        default=default_ram,
        validate=validar_ram
    ).ask().strip()

    cpu = questionary.text(
        f"Número de núcleos CPU [Disponibles: {sis['cpu_log']}]:",
        default=default_cpu,
        validate=validar_cpu
    ).ask().strip()

    # 3. Configuración de Red
    choices_net = [
        "NAT (Automático - Ideal para tener internet de inmediato)",
        "Bridge (Puente - Para que la VM tenga una IP de tu router local)",
        "Ninguna (Máquina desconectada)"
    ]
    tipo_conexion = questionary.select(
        "Tipo de conexión a red:",
        choices=choices_net,
        default=default_net if default_net in choices_net else choices_net[0]
    ).ask()

    modelo_adaptador = ""
    nombre_bridge = ""
    if "Ninguna" not in tipo_conexion:
        if "Bridge" in tipo_conexion:
            bridges = detectar_bridges()
            default_br = config_previa.get("nombre_bridge") if (config_previa and config_previa.get("nombre_bridge") in bridges) else ("br0" if "br0" in bridges else ("virbr0" if "virbr0" in bridges else (bridges[0] if bridges else "br0")))
            if bridges:
                br_choices = bridges + ["Otro nombre..."]
                sel_br = questionary.select(
                    "Selecciona la interfaz puente (bridge):",
                    choices=br_choices,
                    default=default_br
                ).ask()
                if sel_br == "Otro nombre...":
                    nombre_bridge = questionary.text("Nombre de la interfaz bridge:", default=default_br).ask().strip()
                else:
                    nombre_bridge = sel_br
            else:
                nombre_bridge = questionary.text("Nombre de la interfaz bridge:", default="br0").ask().strip()

            permitido, aviso = verificar_permiso_bridge(nombre_bridge)
            if not permitido:
                print(f"\n[!] Advertencia sobre Bridge:")
                print(f"    {aviso}")
                print(f"    Para que QEMU funcione sin root con este bridge, ejecuta en el host:")
                print(f"    echo 'allow {nombre_bridge}' | sudo tee -a /etc/qemu/bridge.conf\n")

        if "Windows" in tipo_os:
            modelo_adaptador = "e1000"
            print("\n[*] Adaptador de red 'e1000' seleccionado automáticamente para Windows.")
        else:
            modelo_adaptador = "virtio-net"
            print("\n[*] Adaptador de red 'virtio-net' seleccionado automáticamente para Linux.")

        usar_legacy = questionary.select(
            "¿Necesitas usar una tarjeta de red legacy (rtl8139) para sistemas antiguos?",
            choices=["No", "Sí"],
        ).ask()

        if usar_legacy == "Sí":
            modelo_adaptador = "rtl8139"
            print("[*] Adaptador cambiado a 'rtl8139' (Legacy).")

    return {
        "nombre": nombre,
        "ram": ram,
        "cpu": cpu,
        "tipo_os": tipo_os,
        "os_folder": os_folder,
        "vm_dir": vm_dir,
        "disco_path": disco_path,
        "tipo_conexion": tipo_conexion,
        "modelo_adaptador": modelo_adaptador,
        "nombre_bridge": nombre_bridge,
        "estado_disco": estado_disco,
    }


def construir_comando(cfg, iso_path=None):
    comando = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m", cfg["ram"],
        "-name", cfg["nombre"],
        "-usb", "-device", "usb-tablet"
    ]

    cpu_val = str(cfg["cpu"]).strip()
    if "Windows" in cfg.get("tipo_os", ""):
        # Windows Desktop (Home/Pro) limita a 1 o 2 sockets físicos; usar cores para aprovechar todos los núcleos
        comando.extend(["-smp", f"{cpu_val},cores={cpu_val},sockets=1"])
    else:
        comando.extend(["-smp", cpu_val])

    if "Windows" in cfg["tipo_os"]:
        comando.extend([
            "-cpu", "host,hv_relaxed,hv_spinlocks=0x1fff,hv_vapic,hv_time",
            "-device", "ich9-ahci,id=ahci",
            "-drive", f"file={cfg['disco_path']},format=qcow2,if=none,id=drive0,discard=unmap",
            "-device", "ide-hd,drive=drive0,bus=ahci.0",
            "-vga", "qxl",
            "-rtc", "base=localtime"
        ])
    else:
        comando.extend([
            "-cpu", "host",
            "-drive", f"file={cfg['disco_path']},format=qcow2,if=virtio,discard=unmap",
            "-vga", "virtio"
        ])

    if "Ninguna" in cfg["tipo_conexion"]:
        comando.extend(["-nic", "none"])
    else:
        if "NAT" in cfg["tipo_conexion"]:
            comando.extend(["-netdev", "user,id=red0"])
        elif "Bridge" in cfg["tipo_conexion"]:
            bridge_name = cfg.get("nombre_bridge") or "br0"
            comando.extend(["-netdev", f"bridge,id=red0,br={bridge_name}"])

        adapter = cfg.get("modelo_adaptador")
        if adapter not in ("virtio-net", "e1000", "rtl8139"):
            adapter = "e1000" if "Windows" in cfg.get("tipo_os", "") else "virtio-net"

        mac = cfg.get("mac") or generar_mac(cfg["nombre"])
        comando.extend(["-device", f"{adapter},netdev=red0,mac={mac}"])

    if iso_path:
        comando.extend(["-cdrom", iso_path, "-boot", "order=c,once=d"])
    else:
        comando.extend(["-boot", "c"])

    return comando


def verificar_dependencias(requiere_sistema=True, requiere_img=True):
    """Verifica la existencia de binarios requeridos en PATH antes de operar."""
    faltantes = []
    if requiere_sistema and not shutil.which("qemu-system-x86_64"):
        faltantes.append("qemu-system-x86_64")
    if requiere_img and not shutil.which("qemu-img"):
        faltantes.append("qemu-img")

    if faltantes:
        print(f"\n[Error Crítico] Herramienta(s) del sistema no encontrada(s): {', '.join(faltantes)}")
        print("[*] Instala los paquetes correspondientes según tu distribución:")
        print("    - Fedora / RHEL:   sudo dnf install qemu-kvm qemu-img")
        print("    - Debian / Ubuntu: sudo apt install qemu-system-x86 qemu-utils")
        print("    - Arch Linux:      sudo pacman -S qemu-base qemu-img")
        print("    - openSUSE:        sudo zypper install qemu-x86 qemu-tools")
        sys.exit(1)


def verificar_kvm():
    """Verifica el estado del módulo KVM y permisos de acceso del usuario."""
    if not os.path.exists("/dev/kvm"):
        print("\n[Advertencia KVM] No se detectó el dispositivo '/dev/kvm'.")
        print("                 QEMU podría fallar al arrancar con aceleración por hardware (-enable-kvm).")
        print("                 -> Comprueba que la virtualización (VT-x / AMD-V) esté habilitada en BIOS/UEFI.")
        return False
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        usuario = os.environ.get("USER", "actual")
        print(f"\n[Advertencia KVM] El usuario '{usuario}' no tiene permisos en '/dev/kvm'.")
        print("                 -> Agrega tu usuario al grupo kvm: sudo usermod -aG kvm $USER")
        print("                 -> Cierra y vuelve a iniciar sesión para aplicar los permisos.")
        return False
    return True


def diagnosticar_fallo_qemu(comando, returncode):
    """Diagnostica las causas más comunes ante salidas con error de QEMU."""
    print(f"\n[!] Error: QEMU finalizó con código de salida: {returncode}")
    print("[-] Diagnóstico de posibles causas:")
    causa_encontrada = False

    if not os.path.exists("/dev/kvm"):
        print("    * /dev/kvm no existe en el sistema. QEMU requiere aceleración KVM (-enable-kvm).")
        print("      -> Activa la virtualización por hardware en BIOS/UEFI o la virtualización anidada.")
        causa_encontrada = True
    elif not os.access("/dev/kvm", os.R_OK | os.W_OK):
        usuario = os.environ.get("USER", "actual")
        print(f"    * El usuario '{usuario}' no tiene permisos de lectura/escritura en /dev/kvm.")
        print(f"      -> Ejecuta: sudo usermod -aG kvm {usuario} (y reinicia la sesión).")
        causa_encontrada = True

    for arg in comando:
        if isinstance(arg, str) and "bridge,id=red0" in arg:
            br_match = re.search(r"br=([^,]+)", arg)
            br_name = br_match.group(1) if br_match else "bridge"
            print(f"    * La VM fue configurada con red Bridge en '{br_name}'.")
            print(f"      -> Comprueba que /etc/qemu/bridge.conf incluya: allow {br_name}")
            print("      -> Comprueba permisos SUID del helper: sudo chmod u+s /usr/libexec/qemu-bridge-helper")
            causa_encontrada = True

    if not causa_encontrada:
        print("    * Revisa los mensajes de error mostrados por QEMU en la terminal para más detalles.")


def ejecutar_qemu(comando):
    print(f"\n[+] Iniciando máquina virtual...")
    verificar_kvm()
    try:
        res = subprocess.run(comando)
        if res.returncode != 0:
            diagnosticar_fallo_qemu(comando, res.returncode)
    except FileNotFoundError:
        print("\n[Error Crítico] No se encontró el ejecutable 'qemu-system-x86_64'.")
        print("[*] Asegúrate de tener instalado QEMU y que se encuentre en tu PATH.")
    except PermissionError as e:
        print(f"\n[Error de Permisos] No se pudo ejecutar QEMU: {e}")
    except KeyboardInterrupt:
        print("\n[!] Cierre forzado de la VM.")


def cmd_install(iso_arg):
    iso_path = iso_arg
    nombre_sug = None

    if iso_path:
        iso_path = os.path.expanduser(iso_path.strip())
        val_res = validar_ruta_iso(iso_path)
        if val_res is not True:
            print(f"\n[Error] {val_res}")
            sys.exit(1)
        nombre_sug = os.path.splitext(os.path.basename(iso_path))[0]
    else:
        iso_dir = os.path.join(QEMU_DIR, "ISO")
        os.makedirs(iso_dir, exist_ok=True)
        isos_en_carpeta = sorted(
            f for f in os.listdir(iso_dir)
            if f.lower().endswith(".iso") and os.path.isfile(os.path.join(iso_dir, f))
        )

        if isos_en_carpeta:
            choices = [
                Choice(title=f"  {f}", value=os.path.join(iso_dir, f))
                for f in isos_en_carpeta
            ] + [
                Separator(),
                Choice(title="Otra ubicación...", value="__other__"),
                Choice(title="Sin ISO (arranque directo desde disco)", value="__none__")
            ]
            seleccion = questionary.select(
                "Selecciona una imagen ISO:", choices=choices
            ).ask()
            if seleccion == "__other__":
                iso_path = questionary.path(
                    "Ruta completa a la imagen .iso:",
                    validate=validar_ruta_iso
                ).ask()
                if iso_path:
                    iso_path = os.path.expanduser(iso_path.strip())
                    nombre_sug = os.path.splitext(os.path.basename(iso_path))[0]
            elif seleccion == "__none__":
                iso_path = None
                nombre_sug = None
            else:
                iso_path = seleccion
                nombre_sug = os.path.splitext(os.path.basename(iso_path))[0]
        else:
            quiere_iso = questionary.confirm(
                "¿Deseas montar una imagen ISO para instalar o arrancar?", default=True
            ).ask()
            if quiere_iso:
                iso_path = questionary.path(
                    "Ruta completa a la imagen .iso:",
                    validate=validar_ruta_iso
                ).ask()
                if iso_path:
                    iso_path = os.path.expanduser(iso_path.strip())
                    nombre_sug = os.path.splitext(os.path.basename(iso_path))[0]

    cfg = pedir_config(nombre_sug)
    estado = cfg.get("estado_disco", "new")

    if estado == "overwrite" or not os.path.exists(cfg["disco_path"]):
        sis = info_sistema()
        tamano = questionary.text(
            f"Tamaño del nuevo disco virtual (ej: 20G, 50G) [Espacio disponible: {sis['disco_disp']}]:",
            validate=validar_tamano_disco
        ).ask().strip() or "20G"
        print(f"[+] Creando disco virtual qcow2 de {tamano}...")
        try:
            subprocess.run(["qemu-img", "create", "-f", "qcow2", cfg["disco_path"], tamano], check=True)
        except subprocess.CalledProcessError as e:
            print(f"\n[Error Crítico] Falló la creación del disco virtual con qemu-img: {e}")
            print("[*] Revisa que tengas espacio suficiente en disco y permisos de escritura en la carpeta.")
            if os.path.exists(cfg["disco_path"]):
                try:
                    os.remove(cfg["disco_path"])
                except OSError:
                    pass
            sys.exit(1)
        except FileNotFoundError:
            print("\n[Error Crítico] No se encontró el comando 'qemu-img'. Asegúrate de tenerlo instalado.")
            sys.exit(1)
    else:
        print(f"\n[+] Reutilizando disco: {cfg['disco_path']}")

    guardar_config(cfg["vm_dir"], cfg)
    comando = construir_comando(cfg, iso_path)
    ejecutar_qemu(comando)


def copiar_con_progreso(origen, destino):
    """Copia un archivo preservando atributos y mostrando una barra de progreso interactiva."""
    origen_abs = os.path.abspath(origen)
    destino_abs = os.path.abspath(destino)

    if origen_abs == destino_abs or (os.path.exists(destino) and os.path.samefile(origen, destino)):
        print(f"\n[*] Origen y destino son el mismo archivo ({destino}). Se omite la copia.")
        return

    total_bytes = os.path.getsize(origen)
    bytes_copiados = 0
    inicio = time.time()
    ultimo_tiempo_update = 0

    print(f"\n[+] Copiando disco ({formatear_tamano(total_bytes)}) a: {destino}")

    if total_bytes == 0:
        shutil.copy2(origen, destino)
        print("[+] Disco copiado exitosamente.")
        return

    try:
        with open(origen, "rb") as f_in, open(destino, "wb") as f_out:
            while True:
                chunk = f_in.read(8 * 1024 * 1024)
                if not chunk:
                    break
                f_out.write(chunk)
                bytes_copiados += len(chunk)

                ahora = time.time()
                if ahora - ultimo_tiempo_update >= 0.1 or bytes_copiados >= total_bytes:
                    ultimo_tiempo_update = ahora
                    porcentaje = (bytes_copiados / total_bytes) * 100
                    transcurrido = ahora - inicio
                    velocidad = (bytes_copiados / transcurrido) if transcurrido > 0 else 0

                    ancho_barra = 25
                    progreso = int((bytes_copiados / total_bytes) * ancho_barra)
                    barra = "=" * max(0, progreso - 1) + (">" if progreso > 0 else "")
                    barra = barra.ljust(ancho_barra)

                    str_copiado = formatear_tamano(bytes_copiados)
                    str_total = formatear_tamano(total_bytes)
                    str_vel = f"{formatear_tamano(velocidad)}/s"

                    if velocidad > 0 and bytes_copiados < total_bytes:
                        seg_restantes = int((total_bytes - bytes_copiados) / velocidad)
                        mins, segs = divmod(seg_restantes, 60)
                        str_eta = f"ETA: {mins:02d}:{segs:02d}"
                    else:
                        str_eta = "Listo"

                    sys.stdout.write(
                        f"\r[+] Progreso: [{barra}] {porcentaje:5.1f}% ({str_copiado}/{str_total}) {str_vel} {str_eta}   "
                    )
                    sys.stdout.flush()

        shutil.copystat(origen, destino)
        sys.stdout.write("\n")
        sys.stdout.flush()
        print("[+] Disco copiado exitosamente.")
    except KeyboardInterrupt:
        print("\n\n[!] Copia cancelada por el usuario.")
        if os.path.exists(destino):
            try:
                os.remove(destino)
            except OSError:
                pass
        sys.exit(0)
    except OSError as e:
        print(f"\n\n[Error Crítico] Falló la copia del archivo: {e}")
        if os.path.exists(destino):
            try:
                os.remove(destino)
            except OSError:
                pass
        sys.exit(1)


def cmd_add(disco_arg):
    disco_origen = disco_arg
    nombre_sug = None

    if disco_origen:
        disco_origen = os.path.expanduser(disco_origen.strip())
        val_res = validar_ruta_disco(disco_origen)
        if val_res is not True:
            print(f"\n[Error] {val_res}")
            sys.exit(1)
        nombre_sug = os.path.splitext(os.path.basename(disco_origen))[0]
    else:
        disco_origen = questionary.path(
            "Ruta al archivo .qcow2 existente:",
            validate=validar_ruta_disco
        ).ask()
        if not disco_origen:
            print("\n[Error] Ruta inválida.")
            sys.exit(1)
        disco_origen = os.path.expanduser(disco_origen.strip())
        nombre_sug = os.path.splitext(os.path.basename(disco_origen))[0]

    cfg = pedir_config(nombre_sug, disco_origen=disco_origen)
    estado = cfg.get("estado_disco", "new")

    if estado == "samefile":
        pass
    elif estado in ("overwrite", "new"):
        copiar_con_progreso(disco_origen, cfg["disco_path"])
    else:
        print(f"\n[+] Reutilizando disco existente: {cfg['disco_path']}")

    guardar_config(cfg["vm_dir"], cfg)
    print(f"\n[+] VM '{cfg['nombre']}' agregada. Usa 'kemu start {cfg['nombre']}' para iniciarla.")


def obtener_info_disco(disco_path):
    """Retorna (tamano_real_bytes, tamano_virtual_bytes) usando qemu-img info."""
    if not os.path.isfile(disco_path):
        return 0, 0
    real_size = os.path.getsize(disco_path)
    try:
        res = subprocess.run(
            ["qemu-img", "info", "--output=json", disco_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            data = json.loads(res.stdout)
            v_size = data.get("virtual-size", real_size)
            a_size = data.get("actual-size", real_size)
            return a_size, v_size
    except Exception:
        pass
    return real_size, real_size


def obtener_vms_existentes():
    vms = []
    if not os.path.isdir(QEMU_DIR):
        return vms

    for os_folder in ("linux", "windows"):
        path = os.path.join(QEMU_DIR, os_folder)
        if not os.path.isdir(path):
            continue
        for f in sorted(os.listdir(path)):
            if f.endswith(".qcow2"):
                disco = os.path.join(path, f)
                if not os.path.isfile(disco):
                    continue
                vm = f[:-6]
                actual_sz, virt_sz = obtener_info_disco(disco)
                if virt_sz > 0 and virt_sz != actual_sz:
                    size_str = f"Capacidad: {formatear_tamano(virt_sz)}, Uso: {formatear_tamano(actual_sz)}"
                else:
                    size_str = formatear_tamano(actual_sz)

                vms.append({
                    "nombre": vm,
                    "os_folder": os_folder,
                    "vm_dir": path,
                    "disco_path": disco,
                    "size_str": size_str,
                })
    return vms


def seleccionar_o_buscar_vm(nombre=None, prompt="Selecciona una VM:"):
    if not os.path.isdir(QEMU_DIR):
        print("[Error] No existe el directorio ~/kemu/")
        sys.exit(1)

    vms = obtener_vms_existentes()
    if not vms:
        print("[!] No hay VMs disponibles en ~/kemu/")
        sys.exit(0)

    if not nombre:
        choices = [
            Choice(
                title=f"{item['nombre']}  ({item['os_folder']}, {item['size_str']})",
                value=item
            )
            for item in vms
        ]
        return questionary.select(prompt, choices=choices).ask()

    nombre_limpio = os.path.splitext(os.path.basename(nombre.strip()))[0]
    coincidencias = [item for item in vms if item["nombre"] == nombre or item["nombre"] == nombre_limpio]
    if not coincidencias:
        print(f"[Error] No se encontró la VM '{nombre}' en ~/kemu/")
        sys.exit(1)

    if len(coincidencias) == 1:
        return coincidencias[0]

    nombre_mostrar = nombre_limpio or nombre
    print(f"\n[!] Se encontraron múltiples VMs con el nombre '{nombre_mostrar}':")
    choices = [
        Choice(
            title=f"{item['nombre']}  ({item['os_folder']}, {item['size_str']})",
            value=item
        )
        for item in coincidencias
    ]
    return questionary.select("Selecciona cuál deseas usar:", choices=choices).ask()


def cmd_start(nombre=None):
    vm_info = seleccionar_o_buscar_vm(nombre, "Selecciona una VM para iniciar:")
    if not vm_info:
        return

    nombre = vm_info["nombre"]
    vm_dir = vm_info["vm_dir"]
    os_folder = vm_info["os_folder"]
    disco_path = vm_info["disco_path"]

    config = cargar_config(vm_dir, nombre)
    if not config:
        print(f"[Error] No se encontró configuración guardada para '{nombre}' en {os_folder}. Usa 'kemu install' o 'kemu add' primero.")
        sys.exit(1)

    tipo_os = "Windows" if os_folder == "windows" else "Linux"

    cfg = {
        "nombre": nombre,
        "ram": config.get("ram", "2G"),
        "cpu": str(config.get("cpu", "2")),
        "tipo_os": tipo_os,
        "disco_path": disco_path,
        "tipo_conexion": config.get("tipo_conexion", "NAT (Recomendado)"),
        "modelo_adaptador": config.get("modelo_adaptador", ""),
        "nombre_bridge": config.get("nombre_bridge", ""),
    }

    comando = construir_comando(cfg)
    ejecutar_qemu(comando)


def cmd_list():
    if not os.path.isdir(QEMU_DIR):
        print("[+] No hay máquinas virtuales en ~/kemu/")
        return

    for os_folder in ("linux", "windows"):
        path = os.path.join(QEMU_DIR, os_folder)
        if not os.path.isdir(path):
            print(f"\n[{os_folder.upper()}] (vacío)")
            continue

        discos = sorted(
            f for f in os.listdir(path)
            if f.endswith(".qcow2") and os.path.isfile(os.path.join(path, f))
        )

        if discos:
            print(f"\n[{os_folder.upper()}]")
            for fname in discos:
                full_path = os.path.join(path, fname)
                actual_sz, virt_sz = obtener_info_disco(full_path)
                if virt_sz > 0 and virt_sz != actual_sz:
                    print(f"  {fname}  (Capacidad: {formatear_tamano(virt_sz)}, Uso en host: {formatear_tamano(actual_sz)})")
                else:
                    print(f"  {fname}  ({formatear_tamano(actual_sz)})")
        else:
            print(f"\n[{os_folder.upper()}] (vacío)")


def cmd_delete(nombre=None):
    vm_info = seleccionar_o_buscar_vm(nombre, "Selecciona la VM a eliminar:")
    if not vm_info:
        return

    nombre = vm_info["nombre"]
    vm_dir = vm_info["vm_dir"]
    os_folder = vm_info["os_folder"]
    disco_path = vm_info["disco_path"]
    config_path = os.path.join(vm_dir, f"{nombre}.json")

    print(f"\nVM: {nombre}")
    print(f"  Carpeta: {os_folder}")
    if os.path.exists(disco_path):
        actual_sz, virt_sz = obtener_info_disco(disco_path)
        if virt_sz > 0 and virt_sz != actual_sz:
            print(f"  Disco: {nombre}.qcow2 (Capacidad: {formatear_tamano(virt_sz)}, Uso en host: {formatear_tamano(actual_sz)})")
        else:
            print(f"  Disco: {nombre}.qcow2 ({formatear_tamano(actual_sz)})")

    print("\n[!] Para confirmar la eliminación, escribe el nombre exacto de la VM.")
    confirm = questionary.text("Escribe el nombre para confirmar:", default="").ask()
    if confirm != nombre:
        print("[!] Eliminación cancelada (el nombre no coincide).")
        return

    try:
        if os.path.exists(disco_path):
            os.remove(disco_path)
        if os.path.exists(config_path):
            os.remove(config_path)
        print(f"[+] VM '{nombre}' ({os_folder}) eliminada.")
    except OSError as e:
        print(f"\n[Error] No se pudo eliminar la VM: {e}")


def cmd_edit(nombre=None):
    vm_info = seleccionar_o_buscar_vm(nombre, "Selecciona la VM a editar:")
    if not vm_info:
        return

    nombre = vm_info["nombre"]
    vm_dir = vm_info["vm_dir"]
    os_folder = vm_info["os_folder"]

    config = cargar_config(vm_dir, nombre)
    if not config:
        print(f"[Error] No se encontró configuración para '{nombre}' en {os_folder}.")
        sys.exit(1)

    print(f"\nEditando configuración de '{nombre}' ({os_folder}):\n")

    ram = questionary.text("Cantidad de RAM:", default=config.get("ram", "2G"), validate=validar_ram).ask().strip()
    cpu = questionary.text("Número de núcleos CPU:", default=str(config.get("cpu", "2")), validate=validar_cpu).ask().strip()

    choices_red = [
        "NAT (Automático - Ideal para tener internet de inmediato)",
        "Bridge (Puente - Para que la VM tenga una IP de tu router local)",
        "Ninguna (Máquina desconectada)"
    ]
    tipo_guardado = config.get("tipo_conexion", "")
    default_red = choices_red[0]
    for ch in choices_red:
        if ch == tipo_guardado:
            default_red = ch
            break
        elif "Bridge" in tipo_guardado and "Bridge" in ch:
            default_red = ch
            break
        elif "Ninguna" in tipo_guardado and "Ninguna" in ch:
            default_red = ch
            break
        elif "NAT" in tipo_guardado and "NAT" in ch:
            default_red = ch
            break

    tipo_conexion = questionary.select(
        "Tipo de conexión a red:",
        choices=choices_red,
        default=default_red
    ).ask()

    modelo_adaptador = config.get("modelo_adaptador", "")
    nombre_bridge = config.get("nombre_bridge", "")

    if "Ninguna" not in tipo_conexion:
        if "Bridge" in tipo_conexion:
            bridges = detectar_bridges()
            guardado_br = config.get("nombre_bridge", "")
            if bridges:
                br_choices = bridges + ["Otro nombre..."]
                if guardado_br and guardado_br in bridges:
                    default_br = guardado_br
                elif "virbr0" in bridges:
                    default_br = "virbr0"
                elif "br0" in bridges:
                    default_br = "br0"
                else:
                    default_br = bridges[0]

                sel_br = questionary.select(
                    "Selecciona la interfaz puente (bridge):",
                    choices=br_choices,
                    default=default_br
                ).ask()
                if sel_br == "Otro nombre...":
                    nombre_bridge = questionary.text("Nombre de la interfaz bridge:", default=guardado_br or "br0").ask().strip()
                else:
                    nombre_bridge = sel_br
            else:
                nombre_bridge = questionary.text("Nombre de la interfaz bridge:", default=guardado_br or "br0").ask().strip()

            permitido, aviso = verificar_permiso_bridge(nombre_bridge)
            if not permitido:
                print(f"\n[!] Advertencia sobre Bridge:")
                print(f"    {aviso}")
                print(f"    Para que QEMU funcione sin root con este bridge, ejecuta en el host:")
                print(f"    echo 'allow {nombre_bridge}' | sudo tee -a /etc/qemu/bridge.conf\n")
        else:
            nombre_bridge = ""

        legacy_default = "Sí" if modelo_adaptador == "rtl8139" else "No"
        usar_legacy = questionary.select(
            "Usar tarjeta de red legacy (rtl8139) para sistemas antiguos:",
            choices=["No", "Sí"],
            default=legacy_default
        ).ask()
        if usar_legacy == "Sí":
            modelo_adaptador = "rtl8139"
        elif os_folder == "windows":
            modelo_adaptador = "e1000"
        else:
            modelo_adaptador = "virtio-net"
    else:
        modelo_adaptador = ""
        nombre_bridge = ""

    confirm = questionary.confirm("¿Guardar cambios?", default=True).ask()
    if not confirm:
        print("[!] Cambios descartados.")
        return

    cfg = {
        "nombre": nombre,
        "ram": ram,
        "cpu": cpu,
        "tipo_conexion": tipo_conexion,
        "modelo_adaptador": modelo_adaptador,
        "nombre_bridge": nombre_bridge,
    }
    guardar_config(vm_dir, cfg)
    print(f"[+] Configuración de '{nombre}' ({os_folder}) guardada.")


def cmd_show(nombre=None):
    vm_info = seleccionar_o_buscar_vm(nombre, "Selecciona la VM a mostrar:")
    if not vm_info:
        return

    nombre = vm_info["nombre"]
    vm_dir = vm_info["vm_dir"]
    os_folder = vm_info["os_folder"]
    disco_path = vm_info["disco_path"]

    config = cargar_config(vm_dir, nombre)

    print(f"\n================================")
    print(f"  VM: {nombre}")
    print(f"================================")
    print(f"  Sistema:       {os_folder}")
    if os.path.exists(disco_path):
        actual_sz, virt_sz = obtener_info_disco(disco_path)
        if virt_sz > 0 and virt_sz != actual_sz:
            print(f"  Disco:         {formatear_tamano(virt_sz)} (Uso en host: {formatear_tamano(actual_sz)})")
        else:
            print(f"  Disco:         {formatear_tamano(actual_sz)}")
    if config:
        print(f"  RAM:           {config.get('ram', 'N/A')}")
        print(f"  CPU:           {config.get('cpu', 'N/A')} núcleos")
        tipo_red = config.get("tipo_conexion", "")
        if "Bridge" in tipo_red and config.get("nombre_bridge"):
            print(f"  Red:           Bridge ({config['nombre_bridge']})")
        elif "Ninguna" in tipo_red:
            print(f"  Red:           Ninguna (Desconectada)")
        elif "NAT" in tipo_red:
            print(f"  Red:           NAT (Acceso a Internet)")
        else:
            print(f"  Red:           {tipo_red}")

        adaptador = config.get("modelo_adaptador")
        if "Ninguna" in tipo_red or not adaptador:
            print(f"  Adaptador:     Ninguno")
        else:
            print(f"  Adaptador:     {adaptador}")
    else:
        print(f"  (sin configuración guardada)")
    print(f"================================")


def cmd_help():
    print("""
 kemu - Asistente de máquinas virtuales QEMU/KVM
============================================================

 USO:
   kemu <comando> [opciones]

 COMANDOS:

   install [<iso>]   Crear e instalar una VM desde ISO
                     Si no se pasa ISO, pregunta interactivamente.
                     El nombre se auto-deriva del archivo ISO.

   add [<disco>]     Agregar una VM desde un disco qcow2 existente
                     Copia el disco a ~/kemu/<os>/ con barra de progreso y guarda la config.
                     No inicia la VM (usa 'start' después).
                     El nombre se auto-deriva del archivo .qcow2.

   start [<nombre>]  Iniciar una VM ya creada
                     Carga RAM, CPU y red guardados en la config.
                     Si se omite el nombre, muestra una lista interactiva.

   list              Listar todas las VMs organizadas por SO

   show [<nombre>]   Mostrar especificaciones de una VM
                     Si se omite el nombre, muestra una lista interactiva.

   edit [<nombre>]   Editar RAM, CPU y red de una VM
                     Muestra los valores actuales pre-cargados.
                     Si se omite el nombre, muestra una lista interactiva.

   delete [<nombre>] Eliminar una VM (disco + config)
                     Pide confirmación antes de borrar.
                     Si se omite el nombre, muestra una lista interactiva.

   help              Mostrar esta ayuda

 ESTRUCTURA DE ARCHIVOS:
   ~/kemu/
   ├── ISO/             (imágenes .iso detectadas automáticamente)
   ├── linux/
   │   ├── <vm>.qcow2   (disco virtual)
   │   └── <vm>.json    (config: RAM, CPU, red)
   └── windows/
       ├── <vm>.qcow2
       └── <vm>.json

 EJEMPLOS:
   kemu install ubuntu.iso
   kemu install
   kemu add disco-viejo.qcow2
   kemu add
   kemu start win10
   kemu start
   kemu list
   kemu show mi-vm
   kemu show
   kemu edit servidor
   kemu edit
   kemu delete test
   kemu delete
""")


def main():
    parser = argparse.ArgumentParser(
        prog="kemu",
        description="Asistente de creación y lanzamiento de máquinas virtuales QEMU/KVM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  kemu install ubuntu.iso
  kemu install
  kemu add disco-viejo.qcow2
  kemu add
  kemu start win10
  kemu start
  kemu list
  kemu show mi-vm
  kemu show
  kemu edit servidor
  kemu edit
  kemu delete test
  kemu delete

Documentación completa: kemu help
"""
    )
    subparsers = parser.add_subparsers(dest="comando", required=True)

    p_install = subparsers.add_parser("install", help="Instalar una VM desde ISO")
    p_install.add_argument("iso", nargs="?", default=None, help="Ruta a la imagen ISO")

    p_add = subparsers.add_parser(
        "add", help="Agregar una VM existente desde un disco qcow2"
    )
    p_add.add_argument(
        "disco", nargs="?", default=None, help="Ruta al disco qcow2 existente (opcional; si se omite, pregunta interactivamente)"
    )

    p_list = subparsers.add_parser("list", help="Listar todas las VMs")

    p_start = subparsers.add_parser("start", help="Iniciar una VM existente")
    p_start.add_argument("nombre", nargs="?", default=None, help="Nombre de la VM a iniciar (opcional; si se omite, muestra lista interactiva)")

    p_edit = subparsers.add_parser("edit", help="Editar configuración de una VM")
    p_edit.add_argument("nombre", nargs="?", default=None, help="Nombre de la VM a editar (opcional; si se omite, muestra lista interactiva)")

    p_show = subparsers.add_parser("show", help="Mostrar especificaciones de una VM")
    p_show.add_argument("nombre", nargs="?", default=None, help="Nombre de la VM a mostrar (opcional; si se omite, muestra lista interactiva)")

    p_delete = subparsers.add_parser("delete", help="Eliminar una VM")
    p_delete.add_argument("nombre", nargs="?", default=None, help="Nombre de la VM a eliminar (opcional; si se omite, muestra lista interactiva)")

    p_help = subparsers.add_parser("help", help="Mostrar ayuda detallada")

    args = parser.parse_args()

    if args.comando == "help":
        cmd_help()
    elif args.comando == "install":
        verificar_dependencias(requiere_sistema=True, requiere_img=True)
        cmd_install(args.iso)
    elif args.comando == "add":
        verificar_dependencias(requiere_sistema=False, requiere_img=True)
        cmd_add(args.disco)
    elif args.comando == "list":
        cmd_list()
    elif args.comando == "start":
        verificar_dependencias(requiere_sistema=True, requiere_img=False)
        cmd_start(args.nombre)
    elif args.comando == "edit":
        cmd_edit(args.nombre)
    elif args.comando == "show":
        cmd_show(args.nombre)
    elif args.comando == "delete":
        cmd_delete(args.nombre)


def cli():
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Asistente cancelado.")
        sys.exit(0)
    except Exception as e:
        if os.environ.get("DEBUG") == "1":
            import traceback
            print(f"\n[!] Error crítico no controlado: {e}", file=sys.stderr)
            traceback.print_exc()
        else:
            print(f"\n[!] Error inesperado: {type(e).__name__}: {e}", file=sys.stderr)
            print("[*] Para ver la traza técnica completa, ejecuta con: DEBUG=1 kemu <comando>", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
