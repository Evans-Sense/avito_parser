# ==============================
# Avito Parser Docker Runner
# ==============================

$IMAGE_NAME = "avito-parser"
$CONTAINER_NAME = "avito-parser-container"

$DATA_PATH = "$PWD\data"
$VOLUME = "${DATA_PATH}:/app/data"

Write-Host "[INFO] Building Docker image..."
docker build -t $IMAGE_NAME .

# Проверка успешности сборки
if ($LastExitCode -ne 0) {
    Write-Error "[ERROR] Failed to build Docker image. Exiting."
    exit 1
}

Write-Host "[INFO] Checking folders..."

if (!(Test-Path $DATA_PATH)) {
    New-Item -ItemType Directory -Path $DATA_PATH | Out-Null
}

if (!(Test-Path "$DATA_PATH\photos")) {
    New-Item -ItemType Directory -Path "$DATA_PATH\photos" | Out-Null
}

if (!(Test-Path "$DATA_PATH\logs")) {
    New-Item -ItemType Directory -Path "$DATA_PATH\logs" | Out-Null
}

Write-Host "[INFO] Removing old container..."
docker rm -f $CONTAINER_NAME 2>$null

Write-Host "[INFO] Starting container..."

docker run `
    -it `
    --name $CONTAINER_NAME `
    -v $VOLUME `
    $IMAGE_NAME

Write-Host "[OK] Container stopped"
