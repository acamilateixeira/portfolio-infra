// Carga do teste de capacidade do TripFlow.
//
// Cada iteração percorre o fluxo inteiro, que exercita os caminhos caros do
// sistema: criar (idempotência no Redis), enviar, aprovar (SELECT ... FOR UPDATE
// no centro de custo + outbox), emitir reservas (lock no Redis + fornecedor
// simulado + outbox) e listar. O relay publica no RabbitMQ e o consumidor grava
// a inbox enquanto a carga roda.
//
// Taxa em degraus (iterações por segundo). O TripFlow limita cada usuário a
// 120 requisições/min; o seed cria 40 viajantes e 10 aprovadores e as iterações
// são distribuídas entre eles, então até 15 it/s ninguém passa de 1,5 req/s.
// Acima disso o teste mediria o rate limit, não a capacidade.
//
// Variáveis: BASE_URL, CC_IDS (ids dos centros de custo, separados por vírgula),
// TRAVELERS, APPROVERS, STAGE_SECONDS, RATES (ex.: "2,5,10,15"), SUMMARY_PATH.

import http from 'k6/http';
import exec from 'k6/execution';
import { check, fail } from 'k6';

const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8000/api';
const TRAVELERS = Number(__ENV.TRAVELERS || 40);
const APPROVERS = Number(__ENV.APPROVERS || 10);
const STAGE_SECONDS = Number(__ENV.STAGE_SECONDS || 120);
const RATES = (__ENV.RATES || '2,5,10,15').split(',').map(Number);
const CC_IDS = (__ENV.CC_IDS || '').split(',').filter(Boolean).map(Number);

const stageName = (rate) => `rate_${String(rate).padStart(2, '0')}`;

const scenarios = {};
const thresholds = {};
RATES.forEach((rate, index) => {
    const name = stageName(rate);
    scenarios[name] = {
        executor: 'constant-arrival-rate',
        rate,
        timeUnit: '1s',
        duration: `${STAGE_SECONDS}s`,
        startTime: `${index * STAGE_SECONDS}s`,
        preAllocatedVUs: Math.max(10, rate * 4),
        maxVUs: Math.max(40, rate * 10),
        gracefulStop: '15s',
    };
    // Limiares folgados só para o k6 incluir as submétricas por degrau no resumo.
    // O teste registra o que mediu; quem decide se o resultado serve é o relatório.
    thresholds[`http_req_duration{scenario:${name}}`] = ['p(99)<60000'];
    thresholds[`http_req_failed{scenario:${name}}`] = ['rate<1'];
    thresholds[`iterations{scenario:${name}}`] = ['count>=0'];
    thresholds[`http_reqs{scenario:${name}}`] = ['count>=0'];
});
for (const step of ['create', 'submit', 'approve', 'book', 'list']) {
    thresholds[`http_req_duration{name:${step}}`] = ['p(99)<60000'];
}

export const options = {
    scenarios,
    thresholds,
    summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    discardResponseBodies: false,
};

function login(email) {
    const res = http.post(`${BASE}/v1/auth/token`, JSON.stringify({ email, password: 'password', device_name: 'capacity' }), {
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        tags: { name: 'login' },
    });
    if (res.status !== 200) fail(`login ${email}: HTTP ${res.status} ${res.body}`);
    return res.json('token');
}

export function setup() {
    if (CC_IDS.length === 0) fail('defina CC_IDS com os centros de custo do seed');
    const pad = (n) => String(n).padStart(2, '0');
    const travelers = [];
    const approvers = [];
    for (let i = 1; i <= TRAVELERS; i++) travelers.push(login(`cap-traveler-${pad(i)}@capacity.test`));
    for (let i = 1; i <= APPROVERS; i++) approvers.push(login(`cap-approver-${pad(i)}@capacity.test`));
    // Momento em que os degraus começam (depois dos logins), para o relatório
    // alinhar as amostras do cgroup com cada degrau.
    return { travelers, approvers, scenariosStartMs: Date.now() };
}

function headers(token, extra = {}) {
    return { headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', 'Content-Type': 'application/json', ...extra } };
}

function isoDate(daysAhead) {
    const d = new Date(Date.now() + daysAhead * 86_400_000);
    return d.toISOString().slice(0, 10);
}

export default function ({ travelers, approvers }) {
    const n = exec.scenario.iterationInTest;
    const traveler = travelers[n % travelers.length];
    const approver = approvers[n % approvers.length];
    const key = () => `${exec.vu.idInTest}-${n}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

    const created = http.post(`${BASE}/v1/travel-requests`, JSON.stringify({
        cost_center_id: CC_IDS[n % CC_IDS.length],
        origin: 'CNF',
        destination: 'GRU',
        departure_on: isoDate(40),
        return_on: isoDate(42),
        estimated_cost_cents: 250_000,
        reason: 'Teste de capacidade do TripFlow.',
    }), { ...headers(traveler, { 'Idempotency-Key': key() }), tags: { name: 'create' } });
    if (!check(created, { 'create 201': (r) => r.status === 201 })) return;
    const id = created.json('data.id');

    const submitted = http.post(`${BASE}/v1/travel-requests/${id}/submit`, null, { ...headers(traveler), tags: { name: 'submit' } });
    if (!check(submitted, { 'submit 200': (r) => r.status === 200 })) return;

    const approved = http.post(`${BASE}/v1/travel-requests/${id}/approve`, null, { ...headers(approver), tags: { name: 'approve' } });
    if (!check(approved, { 'approve 200': (r) => r.status === 200 })) return;

    const booked = http.post(`${BASE}/v1/travel-requests/${id}/bookings`, JSON.stringify({ items: [
        { type: 'flight', supplier: 'LATAM', amount_cents: 180_000 },
        { type: 'hotel', supplier: 'Ibis Paulista', amount_cents: 70_000 },
    ] }), { ...headers(traveler, { 'Idempotency-Key': key() }), tags: { name: 'book' } });
    check(booked, { 'book 200': (r) => r.status === 200 });

    const listed = http.get(`${BASE}/v1/travel-requests?per_page=20`, { ...headers(traveler), tags: { name: 'list' } });
    check(listed, { 'list 200': (r) => r.status === 200 });
}

export function handleSummary(data) {
    // Tokens não vão para o artefato; só o horário de início dos degraus.
    if (data.setup_data) data.setup_data = { scenariosStartMs: data.setup_data.scenariosStartMs };
    return {
        [__ENV.SUMMARY_PATH || 'k6-summary.json']: JSON.stringify(data, null, 2),
        stdout: `\nk6: ${data.metrics.iterations ? data.metrics.iterations.values.count : 0} iterações, ${data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0} requisições\n`,
    };
}
