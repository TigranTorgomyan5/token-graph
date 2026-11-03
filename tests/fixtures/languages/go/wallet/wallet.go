package wallet

import (
	"errors"

	"example.com/wallet/ledger"
)

type IWallet interface {
	Balance() int
	Debit(amount int) error
}

type Wallet struct {
	amount int
}

func NewWallet() *Wallet {
	return &Wallet{amount: 12}
}

func (w *Wallet) Balance() int {
	return w.amount
}

func (w *Wallet) Debit(amount int) error {
	if w.Balance() < amount {
		return errors.New("insufficient funds")
	}
	w.amount -= amount
	ledger.Record("debit")
	return nil
}

type BonusWallet struct {
	*Wallet
	Bonus int
}

func (b *BonusWallet) Balance() int {
	return b.Wallet.Balance() + b.Bonus
}
