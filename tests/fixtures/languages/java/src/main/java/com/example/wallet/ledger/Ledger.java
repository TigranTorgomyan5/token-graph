package com.example.wallet.ledger;

import java.util.ArrayList;
import java.util.List;

public final class Ledger {
    private static final List<String> ENTRIES = new ArrayList<>();

    private Ledger() {
    }

    public static void record(String entry) {
        ENTRIES.add(entry);
    }
}
