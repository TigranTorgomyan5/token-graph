<?php
use Illuminate\Support\Facades\Route;

Route::post('/invoices', [InvoiceController::class, 'store']);
Route::get('invoices/{id}', [InvoiceController::class, 'show']);
// Route::get('/old', ...);
