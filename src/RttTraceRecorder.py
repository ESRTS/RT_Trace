#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
import os
from threading import Thread
import HelperFunctions
import configparser

from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.debug.elf.symbols import ELFSymbolProvider
from pyocd.debug.rtt import RTTControlBlock
from pyocd.flash.file_programmer import FileProgrammer

#configName = "Pico2_FreeRTOS_RTT"

def loadPico2RttTraceBuffers(gui):
    
    thread = Thread(target = rtt_thread, args = (gui,))
    thread.start()

def rtt_thread(gui):
    """
    Thread to flash the target and record the RTT buffers.
    """
    #global configName

    configName = "general"

    cwd = HelperFunctions.getCwd()

    folderName = HelperFunctions.getRecordingFolderName(gui)
    HelperFunctions.makeFolder(folderName)
    filename1 = os.path.abspath(os.path.join(folderName, 'raw_buffer0.txt'))
    filename2 = os.path.abspath(os.path.join(folderName, 'raw_buffer1.txt'))
    
    config = configparser.ConfigParser()
    config.read(HelperFunctions.getConfigFilePath())
    elf = HelperFunctions.getElfFilePath(gui)#config.get(configName,'elf', fallback = None)
    probe = config.get(configName,'probe', fallback = None)
    target = config.get(configName,'target', fallback = 'rp2350')
    frequency = int(config.get(configName,'frequency', fallback = '4_000_000'))
    measure_seconds = float(config.get(configName,'measure_seconds', fallback = '10.0'))

    if elf is None:
        HelperFunctions.printState("ERROR", info="No elf-file configured")
        return 
    
    # Record the trace from the target platform
    record_data(elf, probe, target, frequency, measure_seconds, filename1, filename2)

    # Enable the buttons and update the GUI
    if gui is not None:
        gui.btn_recordTrace.configure(state="normal")
        # Update trace view option menu
        gui.updateTraceViewOptions()
        gui.update()

def parse_int(value: str) -> int:
    return int(value, 0)


def wait_for_halt(core, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if core.get_state() == Target.State.HALTED:
            return True
        time.sleep(0.01)
    return False


def wait_for_pc(core, expected_addr: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    expected = expected_addr & ~1

    while time.monotonic() < deadline:
        if core.get_state() == Target.State.HALTED:
            pc = core.read_core_register("pc") & ~1
            if pc != expected:
                print(f"halted at 0x{pc:08X}, but waiting for 0x{expected_addr:08X}")
            return pc == expected
        time.sleep(0.01)

    return False

def record_data(elf, probe, target, frequency, measure_seconds, filename1, filename2):
    session = ConnectHelper.session_with_chosen_probe(
        unique_id=probe,
        options={
            "target_override": target,
            "frequency": frequency,
            "connect_mode": "halt",
            "resume_on_disconnect": False,
        },
    )

    if session is None:
        print("No debug probe found.", file=sys.stderr)
        return 1

    with session:
        target = session.board.target
        core = target.cores[0]

        print(f"Programming {elf} ...")
        FileProgrammer(session).program(elf)
        print("Programming complete.")

        target.elf = elf
        symbol_provider = ELFSymbolProvider(target.elf)

        main_addr = symbol_provider.get_symbol_value("main")
        hook_addr = symbol_provider.get_symbol_value("trace_init")
        rtt_addr = symbol_provider.get_symbol_value("_SEGGER_RTT")

        if main_addr is None:
            print("Could not resolve symbol: main", file=sys.stderr)
            return 1
        if hook_addr is None:
            print("Could not resolve symbol: trace_init", file=sys.stderr)
            return 1
        if rtt_addr is None:
            print("Could not resolve symbol: _SEGGER_RTT", file=sys.stderr)
            return 1
        
        print(f"main() at 0x{main_addr:08X}")
        print(f"trace_init() at 0x{hook_addr:08X}")
        print(f"_SEGGER_RTT at 0x{rtt_addr:08X}")

        print("Setting breakpoint for trace_init()")
        core.set_breakpoint(hook_addr)

        # Get the target into a known state, then run.
        print("Reset and halt.")
        core.reset_and_halt()
        
        print("Resume and waiting for trace_init() ...")
        core.resume()

        if not wait_for_pc(core, hook_addr, timeout=10.0):
            print("Timed out before reaching trace_init()", file=sys.stderr)
            return 1
        print("Hit trace_init().")
        
        #while core.get_state() == Target.State.HALTED:
        #    print("HALTED...")
        #    pc = core.read_core_register("pc") & ~1
        #    print(f"PC: 0x{pc:08X}")
        #    time.sleep(0.5)


        rtt = RTTControlBlock.from_target(target, address=rtt_addr)
        rtt.start()

        if len(rtt.up_channels) < 3:
            print(f"Need at least 3 RTT up channels, found {len(rtt.up_channels)}", file=sys.stderr)
            return 1
        print("Read information for RTT channel 1 and 2.")
        ch1 = rtt.up_channels[1]
        ch2 = rtt.up_channels[2]

        time.sleep(1)
        print("Remove breakpoint for trace_init().")
        core.remove_breakpoint(hook_addr)
        print(f"Resume and start Trace Record for {measure_seconds} seconds")
        core.resume()

        deadline = time.monotonic() + measure_seconds

        with open(filename1, "wb") as f1, open(filename2, "wb") as f2:
            try:
                while time.monotonic() < deadline:
                    data1 = ch1.read()
                    if data1:
                        #print(data1.decode("ascii", errors="replace"))
                        f1.write(data1)
                        f1.flush()

                    data2 = ch2.read()
                    if data2:
                        #print(data2.decode("ascii", errors="replace"))
                        f2.write(data2)
                        f2.flush()

                    time.sleep(0.001)
            finally:
                print("Finished trace recording, halting the target now.")
                target.halt()

        print(f"Wrote RTT channel 1 to {filename1}")
        print(f"Wrote RTT channel 2 to {filename2}")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--elf", required=True, help="Firmware ELF file")
    ap.add_argument("--probe", default=None, help="Probe unique ID")
    ap.add_argument("--target", default="rp2350", help="Target name")
    ap.add_argument("--frequency", type=parse_int, default=4_000_000)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--ch1-file", default="rtt_test/rtt_ch1.bin", help="Output file for RTT channel 1")
    ap.add_argument("--ch2-file", default="rtt_test/rtt_ch2.bin", help="Output file for RTT channel 2")
    args = ap.parse_args()

    retval = record_data(args.elf, args.probe, args.target, args.frequency, args.seconds, args.ch1_file, args.ch2_file)

    #print(open('rtt_test/rtt_ch1.bin','rb').read().decode('ascii','replace'))
    #print(open('rtt_test/rtt_ch2.bin','rb').read().decode('ascii','replace'))

    return retval


if __name__ == "__main__":
    raise SystemExit(main())