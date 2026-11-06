cmake_minimum_required(VERSION 3.13)

# Add after pico_sdk_init(); the firmware supplies FreeRTOS and its configuration.
if(NOT TARGET pico_runtime OR NOT TARGET hardware_timer OR NOT TARGET hardware_sync)
    message(FATAL_ERROR "Add RT-Trace after pico_sdk_init() in an RP2350 project")
endif()

set(_rt_trace_segger "${PICO_SDK_PATH}/src/rp2_common/pico_stdio_rtt/SEGGER")
if(NOT EXISTS "${_rt_trace_segger}/RTT/SEGGER_RTT.h"
        OR NOT EXISTS "${_rt_trace_segger}/Config/SEGGER_RTT_Conf.h")
    message(FATAL_ERROR "RT-Trace requires the SEGGER RTT copy supplied by the Pico SDK")
endif()

# One SDK RTT implementation/control block. No stdio driver or FreeRTOS code.
add_library(rt_trace_rtt STATIC "${_rt_trace_segger}/RTT/SEGGER_RTT.c")
add_library(RTTrace::RTT ALIAS rt_trace_rtt)
target_link_libraries(rt_trace_rtt PRIVATE pico_base_headers)
target_include_directories(rt_trace_rtt PUBLIC
    "${_rt_trace_segger}/RTT" "${_rt_trace_segger}/Config")
target_compile_definitions(rt_trace_rtt PUBLIC RTT_USE_ASM=0 SEGGER_RTT_MAX_NUM_UP_BUFFERS=3)

# Compile the hooks in the firmware's context so FreeRTOSConfig.h, tick type,
# IRQ setting and scratch-buffer configuration always match that firmware.
add_library(rt_trace INTERFACE)
add_library(RTTrace::Trace ALIAS rt_trace)
target_sources(rt_trace INTERFACE "${CMAKE_CURRENT_LIST_DIR}/trace.c")
target_include_directories(rt_trace INTERFACE "${CMAKE_CURRENT_LIST_DIR}")
target_link_libraries(rt_trace INTERFACE rt_trace_rtt pico_runtime hardware_timer hardware_sync)

function(rt_trace_enable firmware)
    if(NOT TARGET "${firmware}")
        message(FATAL_ERROR "Create the firmware target before rt_trace_enable(${firmware})")
    endif()
    # stdio_init_all() must not reset RTT after the pre-main trace initializer.
    pico_enable_stdio_rtt(${firmware} 0)
    target_link_libraries(${firmware} PRIVATE RTTrace::Trace)
endfunction()
