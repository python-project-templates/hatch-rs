#ifndef HEADER_SYMBOLS_H
#define HEADER_SYMBOLS_H

int header_symbols_answer(void);

/* Declared but not exported: the build must fail. */
int header_symbols_missing(void);

static inline int header_symbols_check(void) {
    return header_symbols_answer() == 42;
}

#endif
