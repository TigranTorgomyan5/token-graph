package com.example.wallet;

import com.example.wallet.ledger.Ledger;

public class Wallet implements IWallet {
    protected int amount = 12;

    @Override
    public int balance() {
        return amount;
    }

    @Override
    public void debit(int amount) {
        if (balance() < amount) {
            throw new IllegalStateException("Insufficient funds");
        }
        this.amount -= amount;
        Ledger.record("debit " + amount);
    }
}
