# このリポジトリ自体を Zephyr モジュールとして読み込ませ、roBa 専用の機能を追加する
if(CONFIG_ROBA_BT_PROFILE_INFO)
  zephyr_library()
  zephyr_library_sources(src/bt_profile_info.c)
  zephyr_include_directories(${APPLICATION_SOURCE_DIR}/include)
endif()
