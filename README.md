# Kemu 🐧⚡

> **Asistente CLI interactivo para creación, configuración y ejecución de máquinas virtuales QEMU/KVM en Linux.**

`kemu` simplifica drásticamente el uso de QEMU y KVM ofreciendo una interfaz interactiva por terminal (TUI) para crear máquinas virtuales (Linux y Windows), configurar CPU, memoria RAM, red (NAT / Bridge), emulación de GPU, arranque UEFI/BIOS y administrar el almacenamiento sin necesidad de memorizar largos comandos de QEMU.

---

## 📋 Requisitos del sistema

Antes de instalar `kemu`, asegúrate de contar con los paquetes del sistema y la virtualización activada:

### 1. Activar virtualización por hardware (KVM)
Verifica que la virtualización esté habilitada en tu BIOS/UEFI (VT-x para Intel o AMD-V para AMD) y que tu usuario pertenezca al grupo `kvm`:

```bash
# Agregar tu usuario al grupo kvm (para no necesitar sudo)
sudo usermod -aG kvm $USER
```
*(Luego cierra sesión y vuelve a entrar, o ejecuta `newgrp kvm`).*

### 2. Instalar QEMU según tu distribución

- **Ubuntu / Debian / Linux Mint:**
  ```bash
  sudo apt update && sudo apt install -y qemu-system-x86 qemu-utils
  ```
- **Fedora / RHEL:**
  ```bash
  sudo dnf install -y qemu-kvm qemu-img
  ```
- **Arch Linux / Manjaro:**
  ```bash
  sudo pacman -S --needed qemu-base qemu-img
  ```
- **openSUSE:**
  ```bash
  sudo zypper install -y qemu-x86 qemu-tools
  ```

---

## 🚀 Instalación con `pipx` (Recomendado)

`pipx` instala `kemu` en un entorno virtual aislado pero hace que el comando `kemu` esté disponible globalmente en tu terminal.

### Paso 1: Instalar `pipx` (si no lo tienes)

- **Ubuntu / Debian:** `sudo apt install pipx && pipx ensurepath`
- **Fedora:** `sudo dnf install pipx && pipx ensurepath`
- **Arch Linux:** `sudo pacman -S python-pipx && pipx ensurepath`

*(Si es la primera vez que instalas `pipx`, reinicia la terminal después de `pipx ensurepath`).*

### Paso 2: Instalar `kemu`

**Opción 1: Directamente desde GitHub (sin clonar manualmente):**
```bash
pipx install git+https://github.com/Diego24Vas/kemu.git
```

**Opción 2: Clonando el repositorio localmente:**
```bash
git clone https://github.com/Diego24Vas/kemu.git
cd kemu
pipx install .
```

¡Listo! Ya puedes ejecutar `kemu` desde cualquier directorio.

### Actualizar o desinstalar
```bash
# Para actualizar a la última versión del repo:
pipx upgrade kemu

# Para desinstalarlo:
pipx uninstall kemu
```

---

## 💡 Alternativa: Instalación en entorno de desarrollo (`venv`)

Si prefieres no usar `pipx` y quieres ejecutarlo en un entorno virtual clásico:

```bash
git clone https://github.com/Diego24Vas/kemu.git
cd kemu

# Crear y activar el entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Ejecutar
python3 main.py --help
```

---

## 🖥️ Uso y Comandos

`kemu` cuenta con un menú interactivo asistido para cada una de sus funciones:

```bash
# Instalar una nueva máquina virtual desde una ISO
kemu install [ruta/a/la/imagen.iso]

# Agregar una VM existente desde un disco .qcow2 previo
kemu add [ruta/al/disco.qcow2]

# Iniciar una máquina virtual existente
kemu start [nombre_vm]

# Listar todas las máquinas virtuales configuradas
kemu list

# Ver las especificaciones y detalles de una máquina virtual
kemu show [nombre_vm]

# Modificar RAM, vCPUs, Red, etc. de una VM existente
kemu edit [nombre_vm]

# Eliminar una máquina virtual y liberar espacio en disco
kemu delete [nombre_vm]

# Mostrar guía y ayuda detallada
kemu help
```

> **Nota:** Si ejecutas cualquiera de los comandos anteriores sin argumentos (por ejemplo, simplemente `kemu start`), se abrirá una lista interactiva para que selecciones la VM que deseas gestionar.

---

## 📁 Ubicación de los datos

Todas las máquinas virtuales se almacenan de manera organizada en:
```text
~/qemu/
├── linux/
│   ├── mi-ubuntu.qcow2   (disco virtual dinámico)
│   └── mi-ubuntu.json    (configuración de hardware y red)
└── windows/
    ├── win10.qcow2
    └── win10.json
```

---

## 📄 Licencia

Distribuido bajo la licencia GNU General Public License v3.0 (GPLv3). Consulta [LICENSE](LICENSE) para más detalles.
