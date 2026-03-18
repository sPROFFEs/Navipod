#!/bin/bash

# ==============================================================================
# Script de Auto-Redimensionado de Disco (Hot-Resize)
# ==============================================================================
# Este script detecta si el disco físico ha crecido (ej. en Proxmox) y extiende
# automáticamente la partición y el sistema de archivos para usar todo el espacio.
#
# Requisitos: growpart (cloud-guest-utils), resize2fs
# Uso: sudo ./resize_disk.sh
# ==============================================================================

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}[*] Iniciando proceso de redimensionado inteligente...${NC}"

# 1. Detectar el dispositivo raiz
ROOT_DEVICE=$(findmnt / -o SOURCE -n)
echo -e "${GREEN}[+] Dispositivo raíz detectado: $ROOT_DEVICE${NC}"

# Obtener nombre del disco padre (ej. /dev/sda1 -> sda)
PARENT_DISK=$(lsblk -no pkname $ROOT_DEVICE)
PARTITION_NUM=$(echo "$ROOT_DEVICE" | grep -o '[0-9]*$')

if [ -z "$PARENT_DISK" ]; then
    echo -e "${RED}[!] Error: No se pudo determinar el disco padre.${NC}"
    exit 1
fi

FULL_PARENT_PATH="/dev/$PARENT_DISK"
echo -e "${GREEN}[+] Disco físico: $FULL_PARENT_PATH${NC}"
echo -e "${GREEN}[+] Partición a extender: #$PARTITION_NUM${NC}"

# 2. Rescanear el bus SCSI para detectar el nuevo tamaño (Hot-Plug)
echo -e "${YELLOW}[*] Rescaneando bus SCSI para detectar cambios de hardware...${NC}"
found_rescan=0
for path in /sys/class/scsi_device/*/device/rescan; do
    if [ -f "$path" ]; then
        echo 1 > "$path"
        found_rescan=1
    fi
done

# Intento alternativo por bloque
if [ -f "/sys/class/block/$PARENT_DISK/device/rescan" ]; then
    echo 1 > "/sys/class/block/$PARENT_DISK/device/rescan"
    found_rescan=1
fi

if [ $found_rescan -eq 1 ]; then
    echo -e "${GREEN}[+] Rescaneo de hardware completado.${NC}"
else
    echo -e "${RED}[!] Alerta: No se encontró ruta de rescan estándar. Intentando continuar...${NC}"
fi

# 3. Extender la tabal de particiones con growpart
echo -e "${YELLOW}[*] Intentando extender partición $PARTITION_NUM en $FULL_PARENT_PATH...${NC}"

if ! command -v growpart &> /dev/null; then
    echo -e "${RED}[!] 'growpart' no instalado. Instalando cloud-guest-utils...${NC}"
    apt-get update && apt-get install -y cloud-guest-utils
fi

OUT=$(growpart "$FULL_PARENT_PATH" "$PARTITION_NUM" 2>&1)
if [[ $OUT == *"NOCHANGE"* ]]; then
    echo -e "${GREEN}[=] La partición ya ocupa todo el espacio disponible.${NC}"
else
    echo -e "${GREEN}[+] Partición extendida correctamente.${NC}"
fi

# 4. Redimensionar el sistema de archivos (Filesystem)
echo -e "${YELLOW}[*] Redimensionando sistema de archivos...${NC}"
resize2fs "$ROOT_DEVICE"

echo -e "${GREEN}[✓] Proceso finalizado. Espacio actual:${NC}"
df -h /
