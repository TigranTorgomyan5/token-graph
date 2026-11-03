import { BonusWallet } from './bonusWallet';

export function debitReducesBalance(): boolean {
  const wallet = new BonusWallet();
  wallet.debit(3);
  return wallet.balance() === 14;
}
