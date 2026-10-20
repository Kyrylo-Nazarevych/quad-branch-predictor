# Minimal bare-metal start-up for Spike (RV32). No OS, no C library.
    .section .text.init, "ax"
    .global _start
_start:
    la    sp, __stack_top        # stack at the end of RAM (see link.ld)
    la    t0, __bss_start        # zero .bss (global variables without an initial value)
    la    t1, __bss_end
1:  bgeu  t0, t1, 2f
    sw    zero, 0(t0)
    addi  t0, t0, 4
    j     1b
2:  call  main
    # Keep main()'s return value in a global (visible in the log as the last store) ...
    la    t0, main_result
    sw    a0, 0(t0)
    # ... and tell Spike to stop with exit status 0: write 1 to the HTIF "tohost" word.
    # (Writing (code << 1) | 1 would make Spike print "*** FAILED ***" for any non-zero code.)
    li    a0, 1
    la    t0, tohost
    sw    a0, 0(t0)
3:  j     3b                     # Spike notices tohost within a few thousand instructions

    .section .bss
    .align 2
    .global main_result
main_result: .word 0

    .section .tohost, "aw", @progbits
    .align 6
    .global tohost
tohost:   .dword 0
    .global fromhost
fromhost: .dword 0
