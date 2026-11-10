<?php

/*
 * Dados do teste de capacidade, criados na imagem de produção do TripFlow.
 *
 * O DatabaseSeeder do TripFlow usa factories com Faker, que é dependência de
 * desenvolvimento e não existe na imagem de produção. Por isso este script cria
 * os registros com forceCreate e valores fixos.
 *
 * Uso: docker compose exec -T api php /capacity/seed.php
 * Variáveis: CAP_TRAVELERS (padrão 40), CAP_APPROVERS (padrão 10), CAP_COST_CENTERS (padrão 5)
 */

declare(strict_types=1);

use App\Models\Company;
use App\Models\CostCenter;
use App\Models\User;
use Illuminate\Contracts\Console\Kernel;
use Illuminate\Support\Facades\Hash;

require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Kernel::class)->bootstrap();

$travelers = (int) (getenv('CAP_TRAVELERS') ?: 40);
$approvers = (int) (getenv('CAP_APPROVERS') ?: 10);
$costCenters = (int) (getenv('CAP_COST_CENTERS') ?: 5);

$company = Company::query()->forceCreate(['name' => 'Capacidade Ltda', 'document' => '00000000000191']);
$password = Hash::make('password');

for ($i = 1; $i <= $costCenters; $i++) {
    // Orçamento alto de propósito: o teste mede capacidade, não a regra de orçamento.
    CostCenter::query()->forceCreate([
        'company_id' => $company->id,
        'code' => sprintf('CAP-%02d', $i),
        'name' => "Capacidade {$i}",
        'monthly_budget_cents' => 1_000_000_000_000,
    ]);
}

$create = function (string $role, int $n) use ($company, $password): void {
    User::query()->forceCreate([
        'company_id' => $company->id,
        'name' => sprintf('%s %02d', ucfirst($role), $n),
        'email' => sprintf('cap-%s-%02d@capacity.test', $role, $n),
        'role' => $role,
        'password' => $password,
    ]);
};

for ($i = 1; $i <= $travelers; $i++) {
    $create('traveler', $i);
}
for ($i = 1; $i <= $approvers; $i++) {
    $create('approver', $i);
}

fwrite(STDOUT, json_encode([
    'company_id' => $company->id,
    'cost_center_ids' => CostCenter::query()->where('company_id', $company->id)->orderBy('id')->pluck('id')->all(),
    'travelers' => $travelers,
    'approvers' => $approvers,
]).PHP_EOL);
