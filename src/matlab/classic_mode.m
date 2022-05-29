clear all;
close all;
clc;

data = importfile('DKSalaries.csv');
range = [1,3,6,9];
data = data(:,range);

PG = [];
SG = [];
SF = [];
PF = [];
C = [];


PG_s = [];
PG_f = [];
SG_s = [];
SG_f = [];
SF_s = [];
SF_f = [];
PF_s = [];
PF_f = [];
C_s = [];
C_f = [];


for i=1:height(data)
    if contains(string(data{i, 1}),'PG')
        PG = [PG, data{i,2}];
        PG_s = [PG_s, data{i,3}];
        PG_f = [PG_f, data{i,4}];
        continue
    elseif contains(string(data{i, 1}),'SG')
        SG = [SG, data{i,2}];
        SG_s = [SG_s, data{i,3}];
        SG_f = [SG_f, data{i,4}];
        continue
    elseif contains(string(data{i, 1}),'SF')
        SF = [SF, data{i,2}];
        SF_s = [SF_s, data{i,3}];
        SF_f = [SF_f, data{i,4}];
        continue
    elseif contains(string(data{i, 1}),'PF')
        PF = [PF, data{i,2}];
        PF_s = [PF_s, data{i,3}];
        PF_f = [PF_f,data{i,4}];
        continue
    elseif contains(string(data{i, 1}),'C')
        C = [C, data{i,2}];
        C_s = [C_s, data{i,3}];
        C_f = [C_f, data{i,4}];
        continue
    end
end
G = [PG, SG]; % Guard
F = [SF, PF]; % Forward;
Players = [G, F, C]; % Players
s = [PG_s, SG_s, SF_s, PF_s, C_s]; % Salary
f = [PG_f, SG_f, SF_f, PF_f, C_f]; % FPPG
[m, n] = size(Players);

PG_index = 1:length(PG);
SG_index = length(PG)+1:length(PG) + length(SF);
G_index = 1:length(G);


SF_index = length(G)+1: length(G) + length(SF);
PF_index = length(G) + length(SF)+1:length(G) + length(SF) + length(PF);
F_index = length(G)+1:length(G) + length(SF) + length(PF);

C_index = length(G) + length(SF) + length(PF) + 1: length(G) + length(SF) + length(PF) + length(C);


% use the solver
cvx_solver Gurobi_2
cvx_save_prefs

cvx_begin
    variable x(n) binary;

    maximize(f * x)
    subject to
        s * x <= 50000;  % salary cap
        sum(x) == 8;  % total 8 players
        
        1 <= sum(x(PG_index)) <= 2; % At least one PG, Maximum two PGs
        1 <= sum(x(SG_index)) <= 2;
        3 <= sum(x(G_index)) <= 4; % At least one SG one PG one G
        
        1 <= sum(x(SF_index)) <= 2; % At least one SF, Maximum two SFs
        1 <= sum(x(PF_index)) <= 2;
        3 <= sum(x(F_index)) <= 4; % At least one SF one PF one F
        
        1 <= sum(x(C_index)) <= 2; % At least one center
        
cvx_end

disp('Players: ')
disp(Players(logical(x>0.5)))

pos_index = find(x>0.5);
pos = [];
for i=1:length(pos_index)
    if ismember(pos_index(i), PG_index)
        pos = [pos,'PG '];
    elseif ismember(pos_index(i), SG_index)
        pos = [pos,'SG '];
    elseif ismember(pos_index(i), SF_index)
        pos = [pos,'SF '];
    elseif ismember(pos_index(i), PF_index)
        pos = [pos,'PF '];
    elseif ismember(pos_index(i), C_index)
        pos = [pos,'C '];
    end
end
disp(['Positions: ', pos])

disp(['FPPG: ', num2str(f(logical(x>0.5)))])

