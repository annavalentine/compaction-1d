# This file contains the functions needed for solving the nonlinear Darcy-Stokes problem.
import numpy as np
from dolfinx.fem import (Function, FunctionSpace, dirichletbc,
                         locate_dofs_topological)
from dolfinx.fem.petsc import NonlinearProblem
from dolfinx.mesh import locate_entities_boundary
from dolfinx.nls.petsc import NewtonSolver
from misc import interp, move_mesh
from mpi4py import MPI
from params import a, alpha, b, dt, eps, nt, nz, phi_min, theta
from petsc4py import PETSc
from ufl import Dx, FiniteElement, TestFunctions, ds, dx, split


def K(phi):
      # 1/permeability 
      return (phi**a)/((1-phi)**b)

def max(f1,f2):
     return 0.5*(f1+f2 + ((f1-f2)**2)**0.5)

def weak_form(w,w_t,w_n,phi,phi_t,phi_n,bc_top):
    # Weak form of the residual for the Darcy-Stokes problem
    w_theta = theta*w + (1-theta)*w_n
    phi_theta = theta*phi + (1-theta)*phi_n

    # weak form of momentum balance:
    F_w =  (eps**2 / K(phi))*w*w_t*dx + (1-phi)*Dx(w,0)*Dx(w_t,0)*dx  + (1-phi)*alpha*w_t*dx

    # add stress BC if w is not prescribed at top boundary:
    if bc_top['type'] == 'stress':
        F_w += bc_top['value']*w_t*ds 

    # weak form of porosity evolution:
    F_phi = (phi-phi_n)*phi_t*dx + dt*w_theta*Dx(phi_theta,0)*phi_t*dx - dt*(1-phi_theta)*Dx(w_theta,0)*phi_t*dx 

    # add constraint phi>phi_min:
    F_phi += (phi-max(phi_min,phi))*phi_t*dx
    return F_w + F_phi


def solve_pde(domain,sol_n,bc_top):
        # Stokes solver for the ice-shelf problem using Taylor-Hood elements

        # Define function space
        P1 = FiniteElement('P',domain.ufl_cell(),1)     
        element = P1*P1
        V = FunctionSpace(domain,element)       

        sol = Function(V)
        (w,phi) = split(sol)
        (w_n,phi_n) = split(sol_n)
        (w_t,phi_t) = TestFunctions(V)

    
        # Mark bounadries of mesh and define a measure for integration
        H = domain.geometry.x.max()
        facets_t = locate_entities_boundary(domain, domain.topology.dim-1, lambda x: np.isclose(x[0],H))
        facets_b = locate_entities_boundary(domain, domain.topology.dim-1, lambda x: np.isclose(x[0],0))
        dofs_t = locate_dofs_topological(V.sub(0), domain.topology.dim-1, facets_t)
        dofs_b = locate_dofs_topological(V.sub(0), domain.topology.dim-1, facets_b)
        bc_b = dirichletbc(PETSc.ScalarType(0), dofs_b,V.sub(0))      # w = 0 at base  

        if bc_top['type'] == 'velocity':
            bc_t = dirichletbc(PETSc.ScalarType(bc_top['value']), dofs_t,V.sub(0))     # w = -1 at top
            bcs = [bc_b,bc_t]
        else:
            bcs = [bc_b]    

        # # Define weak form:
        F = weak_form(w,w_t,w_n,phi,phi_t,phi_n,bc_top)

        # set initial guess for Newton solver to be the solution 
        # from the previous time step:
        sol.sub(0).interpolate(sol_n.sub(0))
        sol.sub(1).interpolate(sol_n.sub(1))
 

        # Solve for sol = (w,phi):
        problem = NonlinearProblem(F, sol, bcs=bcs)
        solver = NewtonSolver(MPI.COMM_WORLD, problem)
        solver.solve(sol)

        # bound porosity below by ~phi_min: 
        #  ** even though we incorporate this in the weak form, min(phi) **  
        #  **   will still drift downwards due to timestepping errors    **
        V0 = FunctionSpace(domain, ("CG", 1))
        Phi = Function(V0)
        Phi.interpolate(sol.sub(1))
        Phi.x.array[Phi.x.array<phi_min] = 1.01*phi_min
        sol.sub(1).interpolate(Phi)
      
        return sol




def solve(domain,initial,bc_top):
    # solve the mixture model given:
    # domain: the computational domain
    # m: melting/freezing rate field 
    # initial: initial conditions 
    # *see example.ipynb for an example of how to set these
    #
    # the solution sol = (u,phii,pe,pw) returns:
    # w: vertical velocity 
    # phi: porosity

    w_arr = np.zeros((nt,nz))
    phi_arr = np.zeros((nt,nz))
    z_arr = np.zeros((nt,nz))

    sol_n = initial

    phi_i = phi_arr
    for i in range(nt):

        print('time step '+str(i+1)+' out of '+str(nt)+' \r',end='')

        # Solve the Darcy-Stokes problem for sol = (phi_i,phi_w,u,p_w,p_e)
        sol = solve_pde(domain,sol_n,bc_top)

        z_i,w_i = interp(sol.sub(0),domain)
        z_i,phi_i = interp(sol.sub(1),domain)

        w_arr[i,:] = w_i
        phi_arr[i,:] = phi_i
        z_arr[i,:] = z_i
        
        domain = move_mesh(domain,sol)

        # set the solution at the previous time step
        sol_n.sub(0).interpolate(sol.sub(0))
        sol_n.sub(1).interpolate(sol.sub(1))
    
    return w_arr,phi_arr,z_arr
