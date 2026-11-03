package com.example.wallet;

public class BonusWallet extends Wallet {
    private final int bonus = 5;

    @Override
    public int balance() {
        return super.balance() + bonus;
    }
}
