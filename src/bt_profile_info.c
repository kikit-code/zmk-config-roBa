/*
 * roBa: BT プロファイル（BT1〜BT5）ごとのペアリング相手を、PC から読めるようにする GATT サービス。
 *
 * 読み出し値（リトルエンディアン、バージョン 1）:
 *   [0]     形式バージョン（1）
 *   [1]     プロファイル数 N
 *   [2]     今選ばれているプロファイルの番号（0 始まり）
 *   [3 + 8*i ...] プロファイル i の情報（8 バイト）
 *       +0  フラグ  bit0 = ペアリング済み, bit1 = 接続中
 *       +1  アドレスの種類（bt_addr_le_t.type）
 *       +2  アドレス 6 バイト（bt_addr_t.val の順＝下位バイトが先）
 *
 * 暗号化された（＝ペアリング済みの）接続からだけ読める。
 * 読み出し側は tools/roba-monitor（model.parse_profiles）。
 */

#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>

#include <zmk/ble.h>

#define PROFILE_INFO_VERSION 1
#define HEADER_SIZE 3
#define ENTRY_SIZE 8

#define FLAG_PAIRED BIT(0)
#define FLAG_CONNECTED BIT(1)

/* tools/roba-monitor/roba_monitor/ble_windows.py の UUID と揃えること */
static const struct bt_uuid_128 service_uuid = BT_UUID_INIT_128(
    BT_UUID_128_ENCODE(0x7a6e0001, 0x52b1, 0x4c3e, 0x9f0a, 0x6d2c8e4b1f30));
static const struct bt_uuid_128 profiles_uuid = BT_UUID_INIT_128(
    BT_UUID_128_ENCODE(0x7a6e0002, 0x52b1, 0x4c3e, 0x9f0a, 0x6d2c8e4b1f30));

static ssize_t read_profiles(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
                             uint16_t len, uint16_t offset) {
    uint8_t data[HEADER_SIZE + ZMK_BLE_PROFILE_COUNT * ENTRY_SIZE];

    data[0] = PROFILE_INFO_VERSION;
    data[1] = ZMK_BLE_PROFILE_COUNT;
    data[2] = (uint8_t)zmk_ble_active_profile_index();

    for (uint8_t i = 0; i < ZMK_BLE_PROFILE_COUNT; i++) {
        uint8_t *entry = &data[HEADER_SIZE + i * ENTRY_SIZE];
        const bt_addr_le_t *addr = zmk_ble_profile_address(i);

        entry[0] = (zmk_ble_profile_is_open(i) ? 0 : FLAG_PAIRED) |
                   (zmk_ble_profile_is_connected(i) ? FLAG_CONNECTED : 0);
        entry[1] = addr->type;
        memcpy(&entry[2], addr->a.val, sizeof(addr->a.val));
    }

    return bt_gatt_attr_read(conn, attr, buf, len, offset, data, sizeof(data));
}

BT_GATT_SERVICE_DEFINE(roba_profile_info, BT_GATT_PRIMARY_SERVICE(&service_uuid),
                       BT_GATT_CHARACTERISTIC(&profiles_uuid.uuid, BT_GATT_CHRC_READ,
                                              BT_GATT_PERM_READ_ENCRYPT, read_profiles, NULL,
                                              NULL), );
