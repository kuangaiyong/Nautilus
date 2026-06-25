#!/bin/sh
# =============================================================================
# Nautilus 私有链 geth (Clique POA) 启动脚本
# -----------------------------------------------------------------------------
# 首次启动：导入/新建签名账户 → 渲染 genesis(注入 extraData + alloc) → geth init
# 后续启动：直接以该账户出块。完全自包含，不在仓库硬编码私钥。
#
# 环境变量：
#   CHAIN_ID            链 ID（默认 13370，需与 genesis.template.json 一致）
#   SIGNER_PRIVATE_KEY  可选。提供则导入该私钥作签名/部署账户；
#                       不提供则首次启动自动生成一个新账户。
#                       生成/导入后的地址写入 $DATADIR/signer.addr，
#                       该账户在 genesis 中预分配 1,000,000 ETH。
# =============================================================================
set -e

DATADIR=/data
TEMPLATE=/genesis/genesis.template.json
PWFILE=/genesis/password.txt
CHAIN_ID="${CHAIN_ID:-13370}"

# 生成 n 个 '0' 字符（纯 POSIX，兼容 busybox sh）
zeros() {
  i=0; s=""
  while [ "$i" -lt "$1" ]; do s="${s}0"; i=$((i + 1)); done
  echo "$s"
}

if [ ! -d "$DATADIR/geth" ]; then
  echo "[privatechain] 首次启动：初始化私有链 (chainId=$CHAIN_ID)"

  if [ -n "$SIGNER_PRIVATE_KEY" ]; then
    echo "[privatechain] 导入提供的签名账户私钥"
    printf '%s' "${SIGNER_PRIVATE_KEY#0x}" > /tmp/signer.key
    SIGNER=$(geth account import --datadir "$DATADIR" --password "$PWFILE" /tmp/signer.key 2>&1 \
             | grep -oiE '[0-9a-f]{40}' | head -1)
    rm -f /tmp/signer.key
  else
    echo "[privatechain] 未提供私钥，自动生成新的签名账户"
    SIGNER=$(geth account new --datadir "$DATADIR" --password "$PWFILE" 2>&1 \
             | grep -oiE '0x[0-9a-f]{40}' | head -1 | sed 's/^0x//')
  fi

  if [ -z "$SIGNER" ]; then
    echo "[privatechain] 错误：无法确定签名账户地址" >&2
    exit 1
  fi
  echo "[privatechain] 签名/部署账户: 0x$SIGNER"

  # Clique extraData = 0x + 32字节 vanity(64个0) + 签名者地址(40) + 65字节 seal(130个0)
  EXTRADATA="0x$(zeros 64)${SIGNER}$(zeros 130)"

  # 渲染 genesis：注入 extraData 与 alloc 中的签名者地址
  sed "s/__EXTRADATA__/${EXTRADATA}/g; s/__SIGNER__/${SIGNER}/g" "$TEMPLATE" > /tmp/genesis.json
  geth init --datadir "$DATADIR" /tmp/genesis.json

  echo "0x$SIGNER" > "$DATADIR/signer.addr"
  echo "[privatechain] 初始化完成"
fi

SIGNER_ADDR=$(cat "$DATADIR/signer.addr")
echo "[privatechain] 以账户 $SIGNER_ADDR 启动出块"

exec geth \
  --datadir "$DATADIR" \
  --networkid "$CHAIN_ID" \
  --http --http.addr 0.0.0.0 --http.port 8545 \
  --http.api eth,net,web3,txpool,debug \
  --http.corsdomain '*' --http.vhosts '*' \
  --ws --ws.addr 0.0.0.0 --ws.port 8546 --ws.api eth,net,web3 \
  --mine --miner.etherbase "$SIGNER_ADDR" \
  --unlock "$SIGNER_ADDR" --password "$PWFILE" --allow-insecure-unlock \
  --miner.gasprice 0 \
  --nodiscover --maxpeers 0 \
  --ipcdisable
