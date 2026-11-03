package wallet

import "testing"

func TestDebitReducesBalance(t *testing.T) {
	w := &BonusWallet{Wallet: NewWallet(), Bonus: 5}
	if err := w.Debit(3); err != nil {
		t.Fatal(err)
	}
	if w.Balance() != 14 {
		t.Fatalf("unexpected balance %d", w.Balance())
	}
}
