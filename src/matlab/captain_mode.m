clear all;
close all;
clc;

data = importfile('DKSalaries (2).csv');
range = [5,3,6,9];
data = data(:,range);

CPT = [];
UTIL = [];

CPT_s = [];
CPT_f = [];
UTIL_s = [];
UTIL_f = [];


for i=1:height(data)
    if contains(string(data{i, 1}),'CPT')
        CPT = [CPT, data{i,2}];
        CPT_s = [CPT_s, data{i,3}];
        CPT_f = [CPT_f, data{i,4}];
        continue
    elseif contains(string(data{i, 1}),'UTIL')
        UTIL = [UTIL, data{i,2}];
        UTIL_s = [UTIL_s, data{i,3}];
        UTIL_f = [UTIL_f, data{i,4}];
        continue
    end
end
max_score = 0;
cpt_player = 'SB';
for i=1:length(CPT)
    temp_UTIL = UTIL(UTIL~=CPT(i));
    temp_UTIL_s = UTIL_s(UTIL~=CPT(i));
    temp_UTIL_f = UTIL_f(UTIL~=CPT(i));
    Players = [CPT(i), temp_UTIL]; % Players
    s = [CPT_s(i), temp_UTIL_s]; % Salary
    f = [CPT_f(i), temp_UTIL_f]; % FPPG
    [m, n] = size(Players);

    UTIL_index = 1 + 1:1 + length(temp_UTIL);


    % use the solver
    cvx_solver Gurobi_2
    cvx_save_prefs

    cvx_begin quiet
        variable x(n) binary;

    maximize(f * x)
    subject to
        s * x <= 50000;  % salary cap
            sum(x) == 6;  % total 6 players
            x(1) == 1; % must pick a captain
    cvx_end


    disp(['Players: ', Players(logical(x>0.5))])
    pos_index = find(x>0.5);
    pos = [];
    for i=1:length(pos_index)
        if i == 1
            pos = [pos,'CPT '];
        elseif ismember(pos_index(i), UTIL_index)
            pos = [pos,'UTIL '];
        end
    end
    disp(['Positions: ', pos])
    disp(['FPPG: ', num2str(f(logical(x>0.5)))])
    
    if (f * x) > max_score
        max_score = f * x;
        cpt_player = Players(1);
    end
end
echo off


disp(['The best captain is: ', cpt_player])

temp_UTIL = UTIL(UTIL~=cpt_player);
temp_UTIL_s = UTIL_s(UTIL~=cpt_player);
temp_UTIL_f = UTIL_f(UTIL~=cpt_player);
Players = [cpt_player, temp_UTIL]; % Players
s = [CPT_s(CPT==cpt_player), temp_UTIL_s]; % Salary
f = [CPT_f(CPT==cpt_player), temp_UTIL_f]; % FPPG
[m, n] = size(Players);

UTIL_index = 1 + 1:1 + length(temp_UTIL);

% use the solver
cvx_solver Gurobi_2
cvx_save_prefs

cvx_begin
    variable x(n) binary;

    maximize(f * x)
    subject to
        s * x <= 50000;  % salary cap
        sum(x) == 6;  % total 6 players
        x(1) == 1; % must pick a captain 
cvx_end


disp(['Players: ', Players(logical(x>0.5))])
pos_index = find(x>0.5);
pos = [];
for i=1:length(pos_index)
    if i == 1
        pos = [pos,'CPT '];
    elseif ismember(pos_index(i), UTIL_index)
        pos = [pos,'UTIL '];
    end
end
disp(['Positions: ', pos])
disp(['FPPG: ', num2str(f(logical(x>0.5)))])