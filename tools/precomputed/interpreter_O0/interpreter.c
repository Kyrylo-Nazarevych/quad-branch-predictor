/* Workload: a tiny bytecode interpreter running a loop of 400 iterations (sum of squares).
   The decoder is an if-chain on purpose: a switch would become a jump table, an indirect jump the chip does not
   predict. Every decoded instruction walks the chain, so many static branches run with patterns that follow the
   bytecode, and they compete for the chip's 64 table entries and 8 perceptrons. */
enum { PUSH, LOAD, STORE, ADD, SUB, MUL, JNZ, HALT };

static const int code[] = {
    PUSH, 400, STORE, 0,                   /* r0 = 400   loop counter */
    PUSH, 0, STORE, 1,                     /* r1 = 0     sum */
    LOAD, 0, LOAD, 0, MUL,                 /* loop: push r0 * r0 */
    LOAD, 1, ADD, STORE, 1,                /* r1 += it */
    LOAD, 0, PUSH, 1, SUB, STORE, 0,       /* r0 -= 1 */
    LOAD, 0, JNZ, 8,                       /* while r0 != 0 */
    HALT
};

static int run(void) {
    int stack[16], sp = 0, reg[4] = {0, 0, 0, 0}, pc = 0;
    for (;;) {
        int op = code[pc++];
        if (op == PUSH)       stack[sp++] = code[pc++];
        else if (op == LOAD)  stack[sp++] = reg[code[pc++]];
        else if (op == STORE) reg[code[pc++]] = stack[--sp];
        else if (op == ADD)   { sp--; stack[sp - 1] += stack[sp]; }
        else if (op == SUB)   { sp--; stack[sp - 1] -= stack[sp]; }
        else if (op == MUL)   { sp--; stack[sp - 1] *= stack[sp]; }
        else if (op == JNZ)   { int t = code[pc++]; if (stack[--sp] != 0) pc = t; }
        else                  return reg[1];
    }
}

int main(void) {
    return run() & 0x7f;                   /* sum of k*k for k = 1..400 = 21,413,400 */
}
